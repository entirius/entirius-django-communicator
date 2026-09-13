# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""AI drafting over the toolbox: prompt split, one completion, output parsing, failure codes and notification.

Transport only through `django_utils.toolbox.ToolboxClient` — `complete()` makes exactly one HTTP attempt.
Prompts and keys are never logged and never put into `failure_detail`.
"""

import logging
from dataclasses import dataclass

from django.apps import apps
from django_utils.toolbox import (
    CompletionRequest,
    ToolboxBudgetExceededError,
    ToolboxClient,
    ToolboxError,
    ToolboxModelNotAllowedError,
    ToolboxValidationError,
)
from django_utils.toolbox import Message as PromptMessage
from pydantic import BaseModel, Field, ValidationError

from django_communicator.enums import FailureCode
from django_communicator.models import MessageTemplateVersion

logger = logging.getLogger(__name__)

USER_PROMPT_SEPARATOR = "=== USER ==="
_HIGH_SEVERITY_CODES = frozenset({FailureCode.BUDGET.value, FailureCode.MODEL.value})


class DraftOutputError(Exception):
    """The toolbox answered, but not with `{subject, body_paragraphs[]}`."""


class DraftOutput(BaseModel):
    subject: str = Field(min_length=1)
    body_paragraphs: list[str] = Field(min_length=1)


@dataclass(frozen=True)
class Draft:
    subject: str
    body_text: str
    model: str
    usage: dict
    attempts: int


def prompt_messages(prompt: str) -> list[PromptMessage]:
    """`system === USER === user` → [system, user]; a prompt without the separator is one user message."""
    system, separator, user = prompt.partition(f"\n{USER_PROMPT_SEPARATOR}\n")
    if not separator:
        return [PromptMessage(role="user", content=prompt)]
    return [PromptMessage(role="system", content=system), PromptMessage(role="user", content=user)]


def generate(*, prompt: str, version: MessageTemplateVersion, tag: str, channel_idx: str) -> Draft:
    """One completion. Raises `ToolboxError` (transport / toolbox) or `DraftOutputError` (unusable output)."""
    request = CompletionRequest(
        model=version.model,
        messages=prompt_messages(prompt),
        json_schema=version.json_schema,
        tags=[tag, f"channel:{channel_idx}"],
    )
    with ToolboxClient() as client:
        response = client.complete(request)
    try:
        output = DraftOutput.model_validate(response.parsed or {})
    except ValidationError:
        raise DraftOutputError("draft output does not match {subject, body_paragraphs}") from None
    usage = {**response.usage.model_dump(), "cost": str(response.cost) if response.cost is not None else None}
    body_text = "\n\n".join(output.body_paragraphs)
    return Draft(output.subject, body_text, response.model, usage, response.attempts)


def failure_code(error: Exception) -> str:
    """402 → budget, 400/422 or unusable output → schema, 403 MODEL_NOT_ALLOWED → model, anything else → upstream."""
    if isinstance(error, ToolboxBudgetExceededError):
        return FailureCode.BUDGET
    if isinstance(error, ToolboxValidationError | DraftOutputError):
        return FailureCode.SCHEMA
    if isinstance(error, ToolboxModelNotAllowedError):
        return FailureCode.MODEL
    return FailureCode.UPSTREAM


def failure_detail(error: Exception) -> str:
    """Error class, toolbox code, HTTP status and field names — never the prompt, the message body or the key."""
    if not isinstance(error, ToolboxError):
        return type(error).__name__
    fields = ",".join(sorted(error.field_errors))
    return f"{type(error).__name__} {error.code or '-'} HTTP {error.status_code}" + (
        f" fields={fields}" if fields else ""
    )


def notify_failure(*, channel_idx: str, subject_ref: str, code: str, template_key: str) -> None:
    """Soft dependency on django_notifications: without it, or without its channel, only a warning is logged."""
    if not apps.is_installed("django_notifications"):
        logger.warning("communicator draft failed (%s) for %s; notifications not installed", code, subject_ref)
        return
    from django_notifications.services.notify_service import notify

    severity = "high" if code in _HIGH_SEVERITY_CODES else "medium"
    title = f"Message draft failed: {code} ({template_key})"
    try:
        notify(
            channel_idx=channel_idx,
            recipient_role="sales_admin",
            severity=severity,
            subject_ref=subject_ref,
            title=title,
        )
    except Exception:  # noqa: BLE001 — a notification failure must not hide the draft failure
        logger.warning("communicator could not notify about a failed draft for %s", subject_ref, exc_info=True)
