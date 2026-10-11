"""Sortable, prefixed identifiers: ``<prefix>_<26-char Crockford ULID>`` (contract §1).

48 bits of millisecond time + 80 random bits, so ids sort by creation time and never collide in
practice. Ids are opaque handles: they carry no value, so they may appear in logs and URLs.
"""

from __future__ import annotations

import os
import re
import time

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ID_RE = re.compile(r"^[a-z][a-z0-9]{0,7}_[0-9A-HJKMNP-TV-Z]{26}$")
_PREFIX_RE = re.compile(r"^[a-z][a-z0-9]{0,7}$")
PREFIX_CONNECTION, PREFIX_JOB, PREFIX_SCAN, PREFIX_SNAPSHOT = "c", "j", "s", "snap"
PREFIX_GROUP, PREFIX_ARTEFACT, PREFIX_EVIDENCE = "grp", "art", "ev"


def ulid(now_ms: int | None = None) -> str:
    """A 26-character Crockford base32 ULID (``now_ms`` pins the time part, for tests)."""
    ms = int(time.time() * 1000) if now_ms is None else now_ms
    value = (ms & ((1 << 48) - 1)) << 80 | int.from_bytes(os.urandom(10), "big")
    return "".join(CROCKFORD[(value >> (5 * i)) & 31] for i in reversed(range(26)))


def new_id(prefix: str) -> str:
    """``<prefix>_<ulid>``; ``ValueError`` for a malformed prefix."""
    if not _PREFIX_RE.fullmatch(prefix):
        raise ValueError("id prefix must match ^[a-z][a-z0-9]{0,7}$")
    return f"{prefix}_{ulid()}"


def is_id(value: str, prefix: str | None = None) -> bool:
    """True when ``value`` is a well-formed id (with ``prefix`` when given)."""
    return bool(ID_RE.fullmatch(value)) and (prefix is None or value.split("_", 1)[0] == prefix)
