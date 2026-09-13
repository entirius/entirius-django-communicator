# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Registrable-domain normalisation without a public-suffix download (same rule as siteintel and leads dedup).

eTLD+1 = the last two host labels, or three when the last two form a known multi-part public suffix
(`shop.com.pl`). Hosts without a dot and IP literals are their own key. Never import this across modules.
"""

import ipaddress

MULTI_PART_SUFFIXES = frozenset(
    {"com.pl", "net.pl", "org.pl", "co.uk", "org.uk", "com.au", "co.jp", "com.br", "co.nz", "com.tr", "co.za"}
)


def registrable_domain(host: str) -> str:
    """`www.shop.pl` → `shop.pl`; raises `ValueError` on a host without usable labels."""
    host = host.strip().lower().rstrip(".")
    if not host:
        raise ValueError("not a domain")
    if _is_ip(host) or "." not in host:
        return host
    labels = [label for label in host.split(".") if label]
    if len(labels) < 2 or any(" " in label for label in labels):
        raise ValueError("not a domain")
    size = 3 if ".".join(labels[-2:]) in MULTI_PART_SUFFIXES and len(labels) > 2 else 2
    return ".".join(labels[-size:])


def email_domain(email: str) -> str:
    """Registrable domain of an email address; raises `ValueError` when there is no host part."""
    _, _, host = email.strip().rpartition("@")
    if not host:
        raise ValueError("not an email address")
    return registrable_domain(host)


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True
