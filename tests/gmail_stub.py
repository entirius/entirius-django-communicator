# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""A local stand-in for the parts of Google the communicator uses: OAuth consent + token endpoints and the Gmail API
(profile, messages.send, messages.list/get, history.list). Standard library only, no network beyond its own socket.

    python tests/gmail_stub.py --port 9099          # standalone (the BDD runs use it in its own container)

or in a test: `with running_stub() as stub: stub.url ...`. `/__stub/...` are the control endpoints (inject an inbox
message, read what was sent, revoke a token, make the next sends fail, expire history). Nothing here is secure on
purpose: it is only started on localhost / a private docker network for tests.
"""

import argparse
import base64
import hashlib
import itertools
import json
import secrets
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

SEND = "https://www.googleapis.com/auth/gmail.send"
READ = "https://www.googleapis.com/auth/gmail.readonly"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class State:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self.codes: dict[str, dict] = {}
            self.refresh: dict[str, dict] = {}
            self.access: dict[str, dict] = {}
            self.boxes: dict[str, dict] = {}
            self.counter = itertools.count(1000)
            self.config = {"identity_override": "", "drop_scopes": [], "history_expired_before": 0, "access_ttl": 3600}
            self.fail_send: list[tuple[int, str]] = []
            self.last_auth: dict = {}
            self.calls: list[str] = []

    def box(self, email: str) -> dict:
        return self.boxes.setdefault(email.lower(), {"sent": [], "inbox": []})

    def add_inbox(self, email: str, raw: bytes) -> dict:
        item = {"id": f"in{next(self.counter)}", "raw": raw, "history": str(next(self.counter)), "labels": ["INBOX"]}
        self.box(email)["inbox"].append(item)
        return item

    def head(self, email: str) -> str:
        items = self.box(email)["inbox"] + self.box(email)["sent"]
        return str(max([int(i["history"]) for i in items] + [1000]))


STATE = State()


class Handler(BaseHTTPRequestHandler):
    server_version = "GmailStub/1"

    def log_message(self, *args) -> None:  # silent
        pass

    # -- helpers
    def _body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def _json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: int, reason: str, message: str = "stub error") -> None:
        self._json(status, {"error": {"code": status, "message": message, "errors": [{"reason": reason}]}})

    def _token_for(self, *scopes: str) -> dict | None:
        auth = self.headers.get("Authorization", "")
        info = STATE.access.get(auth.removeprefix("Bearer ").strip())
        if info is None or info["revoked"]:
            self._error(401, "authError", "Invalid Credentials")
            return None
        if scopes and not any(scope in info["scope"].split() for scope in scopes):
            self._error(403, "insufficientPermissions", "Request had insufficient authentication scopes.")
            return None
        return info

    @staticmethod
    def _record(method: str, path: str) -> None:
        if not path.startswith("/__stub/"):
            STATE.calls.append(f"{method} {path}")

    # -- routing
    def do_GET(self) -> None:  # noqa: N802
        url = urlsplit(self.path)
        query = {key: values[-1] for key, values in parse_qs(url.query).items()}
        self._record("GET", url.path)
        with STATE.lock:
            if url.path == "/auth":
                return self._auth(query)
            if url.path.startswith("/__stub/"):
                return self._control_get(url.path, query)
            if url.path.startswith("/gmail/v1/users/me/"):
                return self._gmail_get(url.path.removeprefix("/gmail/v1/users/me/"), query)
        self._error(404, "notFound")

    def do_POST(self) -> None:  # noqa: N802
        url = urlsplit(self.path)
        body = self._body()
        self._record("POST", url.path)
        with STATE.lock:
            if url.path == "/token":
                return self._token({k: v[-1] for k, v in parse_qs(body.decode()).items()})
            if url.path == "/revoke":
                return self._revoke({k: v[-1] for k, v in parse_qs(body.decode()).items()})
            if url.path.startswith("/__stub/"):
                return self._control_post(url.path, json.loads(body or b"{}"))
            if url.path == "/gmail/v1/users/me/messages/send":
                return self._send(json.loads(body or b"{}"))
        self._error(404, "notFound")

    # -- OAuth
    def _auth(self, query: dict) -> None:
        STATE.last_auth = query
        wanted = {
            "response_type": "code",
            "access_type": "offline",
            "prompt": "consent",
            "code_challenge_method": "S256",
        }
        if not query.get("client_id") or any(query.get(k) != v for k, v in wanted.items()) or not query.get("state"):
            return self._error(400, "invalid_request", "bad authorization request")
        email = (STATE.config["identity_override"] or query.get("login_hint", "")).lower()
        scopes = [s for s in query.get("scope", "").split() if s not in STATE.config["drop_scopes"]]
        code = "code-" + secrets.token_hex(8)
        STATE.codes[code] = {"email": email, "scope": " ".join(scopes), "challenge": query.get("code_challenge", "")}
        target = query["redirect_uri"] + ("&" if "?" in query["redirect_uri"] else "?")
        target += urlencode({"code": code, "state": query["state"]})
        self.send_response(302)
        self.send_header("Location", target)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _grant(self, email: str, scope: str, refresh_token: str) -> dict:
        token = "at-" + secrets.token_hex(12)
        STATE.access[token] = {"email": email, "scope": scope, "revoked": False}
        claims = _b64(json.dumps({"email": email, "email_verified": True}).encode()).rstrip("=")
        return {
            "access_token": token,
            "refresh_token": refresh_token,
            "expires_in": STATE.config["access_ttl"],
            "scope": scope,
            "token_type": "Bearer",
            "id_token": f"{_b64(b'{}').rstrip('=')}.{claims}.sig",
        }

    def _token(self, form: dict) -> None:
        if not form.get("client_id") or not form.get("client_secret"):
            return self._json(401, {"error": "invalid_client"})
        if form.get("grant_type") == "authorization_code":
            info = STATE.codes.pop(form.get("code", ""), None)
            challenge = _b64(hashlib.sha256(form.get("code_verifier", "").encode()).digest()).rstrip("=")
            if info is None or info["challenge"] != challenge:
                return self._json(400, {"error": "invalid_grant"})
            refresh = "rt-" + secrets.token_hex(12)
            STATE.refresh[refresh] = {"email": info["email"], "scope": info["scope"], "revoked": False}
            return self._json(200, self._grant(info["email"], info["scope"], refresh))
        if form.get("grant_type") == "refresh_token":
            info = STATE.refresh.get(form.get("refresh_token", ""))
            if info is None or info["revoked"]:
                return self._json(
                    400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}
                )
            reply = self._grant(info["email"], info["scope"], "")
            reply.pop("refresh_token"), reply.pop("id_token")
            return self._json(200, reply)
        self._json(400, {"error": "unsupported_grant_type"})

    def _revoke(self, form: dict) -> None:
        token = form.get("token", "")
        for table in (STATE.refresh, STATE.access):
            if token in table:
                table[token]["revoked"] = True
                if table is STATE.refresh:
                    for access in STATE.access.values():
                        if access["email"] == table[token]["email"]:
                            access["revoked"] = True
                return self._json(200, {})
        self._json(400, {"error": "invalid_token"})

    # -- Gmail
    def _gmail_get(self, path: str, query: dict) -> None:
        if path == "profile":
            info = self._token_for(READ, "https://www.googleapis.com/auth/gmail.modify", "https://mail.google.com/")
            if info is None:
                return
            return self._json(200, {"emailAddress": info["email"], "historyId": STATE.head(info["email"])})
        info = self._token_for(READ)
        if info is None:
            return
        box = STATE.box(info["email"])
        if path == "messages":
            label = query.get("labelIds")
            items = [i for i in box["inbox"] if not label or label in i["labels"]]
            items = list(reversed(items))[: int(query.get("maxResults", 100))]
            return self._json(200, {"messages": [{"id": i["id"], "threadId": i["id"]} for i in items]})
        if path.startswith("messages/"):
            wanted = path.removeprefix("messages/")
            for item in box["inbox"]:
                if item["id"] == wanted:
                    return self._json(
                        200,
                        {
                            "id": wanted,
                            "labelIds": item["labels"],
                            "raw": _b64(item["raw"]),
                            "historyId": item["history"],
                        },
                    )
            return self._error(404, "notFound", "Requested entity was not found.")
        if path == "history":
            start = int(query.get("startHistoryId", "0"))
            if start < STATE.config["history_expired_before"]:
                return self._error(404, "notFound", "Requested entity was not found.")
            history = [
                {"id": i["history"], "messagesAdded": [{"message": {"id": i["id"], "labelIds": i["labels"]}}]}
                for i in box["inbox"]
                if int(i["history"]) > start
            ]
            reply = {"historyId": STATE.head(info["email"])}
            if history:
                reply["history"] = history
            return self._json(200, reply)
        self._error(404, "notFound")

    def _send(self, payload: dict) -> None:
        info = self._token_for(SEND)
        if info is None:
            return
        if STATE.fail_send:
            status, reason = STATE.fail_send.pop(0)
            return self._error(status, reason)
        raw = _unb64(payload.get("raw", ""))
        item = {"id": f"s{next(STATE.counter)}", "raw": raw, "history": str(next(STATE.counter)), "labels": ["SENT"]}
        STATE.box(info["email"])["sent"].append(item)
        self._json(200, {"id": item["id"], "threadId": item["id"], "labelIds": ["SENT"]})

    # -- control
    def _control_get(self, path: str, query: dict) -> None:
        if path == "/__stub/sent":
            sent = STATE.box(query.get("email", ""))["sent"]
            return self._json(
                200, {"sent": [{"id": i["id"], "raw": i["raw"].decode("utf-8", "replace")} for i in sent]}
            )
        if path == "/__stub/last_auth":
            return self._json(200, STATE.last_auth)
        if path == "/__stub/calls":
            return self._json(200, {"calls": STATE.calls})
        self._error(404, "notFound")

    def _control_post(self, path: str, body: dict) -> None:
        if path == "/__stub/reset":
            STATE.reset()
            return self._json(200, {})
        if path == "/__stub/inject":
            item = STATE.add_inbox(body["email"], body["raw"].encode())
            return self._json(200, {"id": item["id"]})
        if path == "/__stub/revoke":  # as if the user removed the app's access in the Google account
            for info in list(STATE.refresh.values()) + list(STATE.access.values()):
                if info["email"] == body["email"].lower():
                    info["revoked"] = True
            return self._json(200, {})
        if path == "/__stub/expire_access":  # the cached access tokens are no longer valid
            for info in STATE.access.values():
                if info["email"] == body["email"].lower():
                    info["revoked"] = True
            return self._json(200, {})
        if path == "/__stub/fail_send":
            STATE.fail_send.extend(
                [(int(body["status"]), body.get("reason", "backendError"))] * int(body.get("count", 1))
            )
            return self._json(200, {})
        if path == "/__stub/config":
            STATE.config.update(body)
            return self._json(200, STATE.config)
        self._error(404, "notFound")


class StubServer:
    def __init__(self, port: int = 0, host: str = "127.0.0.1") -> None:
        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def start(self) -> "StubServer":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@contextmanager
def running_stub():
    STATE.reset()
    server = StubServer().start()
    try:
        yield server
    finally:
        server.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104 - a test double inside a private docker network
    parser.add_argument("--port", type=int, default=9099)
    args = parser.parse_args()
    server = StubServer(args.port, args.host)
    print(f"gmail stub on {args.host}:{args.port}", flush=True)
    server.httpd.serve_forever()
