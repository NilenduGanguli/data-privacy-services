"""At-rest sealing for job secrets and inline connection credentials (contract §6.3).

``seal`` = ``b"\\x01" + nonce(12) + AES-256-GCM(kek, plaintext, aad)``. The AAD binds a blob to
its row (``"dps_job.key:<job id>"``, ``"dps_job.spec:<job id>"``, ``"dps_connection.secret:<id>"``),
so a sealed blob copied into another row does not open. This module and
``dps/engine/transform/crypto.py`` are the only modules that touch key bytes; neither logs, and
every error here is detail-free.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import BaseModel, ValidationError

from dps.config import Settings

_VERSION = b"\x01"
_NONCE = 12
_TAG = 16
_KEK_BYTES = 32
#: The only file names read from a ``secret_ref`` directory; anything else there is ignored.
SECRET_FILES = (
    "username",
    "password",
    "dsn",
    "uri",
    "access_key_id",
    "secret_access_key",
    "storage_state.json",
)


class SealError(Exception):
    """A sealed blob could not be opened (wrong KEK, wrong row, tampered). Carries no detail."""

    def __init__(self) -> None:
        super().__init__("sealed blob could not be opened")


class SecretUnavailable(Exception):
    """A ``secret_ref`` does not resolve to a readable secret directory."""

    code = "secret_unavailable"

    def __init__(self) -> None:
        super().__init__("secret reference could not be resolved")


def _b64decode(text: str) -> bytes:
    text = text.strip()
    pad = "=" * (-len(text) % 4)
    alt = b"-_" if ("-" in text or "_" in text) else None
    return base64.b64decode(text + pad, altchars=alt, validate=True)


def load_kek(settings: Settings) -> bytes | None:
    """The 32-byte job KEK, or ``None`` when unset (settings already validated its length)."""
    return _b64decode(settings.job_kek) if settings.job_kek else None


def seal(kek: bytes, plaintext: bytes, *, aad: str) -> bytes:
    """Encrypt and authenticate ``plaintext`` bound to ``aad`` (AES-256: the KEK is 32 bytes)."""
    if len(kek) != _KEK_BYTES:
        raise ValueError("the job KEK must be 32 bytes")
    nonce = os.urandom(_NONCE)
    return _VERSION + nonce + AESGCM(kek).encrypt(nonce, plaintext, aad.encode("utf-8"))


def open_sealed(kek: bytes, sealed: bytes, *, aad: str) -> bytes:
    """Decrypt a blob from :func:`seal`; :class:`SealError` on any failure."""
    if len(sealed) < 1 + _NONCE + _TAG or sealed[:1] != _VERSION or len(kek) != _KEK_BYTES:
        raise SealError()
    try:
        return AESGCM(kek).decrypt(
            sealed[1 : 1 + _NONCE], sealed[1 + _NONCE :], aad.encode("utf-8")
        )
    except (InvalidTag, ValueError):
        raise SealError() from None


def seal_json(kek: bytes, model: BaseModel, *, aad: str) -> bytes:
    """Seal a pydantic model's JSON (``SecretStr`` fields must be revealed by the caller first)."""
    return seal(kek, model.model_dump_json().encode("utf-8"), aad=aad)


def open_json[M: BaseModel](kek: bytes, sealed: bytes, model: type[M], *, aad: str) -> M:
    """Open and validate a sealed model; :class:`SealError` on any failure."""
    try:
        return model.model_validate_json(open_sealed(kek, sealed, aad=aad))
    except ValidationError:
        raise SealError() from None


def resolve_secret_ref(ref: str, settings: Settings) -> dict[str, str]:
    """Read the allow-listed files under ``DPS_SECRET_DIR/<ref>/`` (trailing newline stripped)."""
    if not ref or ref.startswith("/") or ".." in ref.split("/"):
        raise SecretUnavailable()
    root = Path(settings.secret_dir).resolve()
    folder = (root / ref).resolve()
    if root not in folder.parents or not folder.is_dir():
        raise SecretUnavailable()
    out: dict[str, str] = {}
    for name in SECRET_FILES:
        path = folder / name
        if path.is_file():
            try:
                out[name] = path.read_text(encoding="utf-8").rstrip("\r\n")
            except OSError:
                raise SecretUnavailable() from None
    if not out:
        raise SecretUnavailable()
    return out


def keyed_fingerprint(kek: bytes, *, info: str, salt: str, data: bytes, hex_chars: int = 8) -> str:
    """``hex_chars`` hex digits of ``HMAC-SHA256(HKDF-SHA256(kek, salt, info), data)``.

    A handle for a customer value (a checkpoint key) that nobody without the KEK can compute, so
    enumerating a small value space (integer ids) cannot test candidates against it; the salt
    (a job id) keeps one value from correlating across jobs. The subkey never leaves here.
    """
    if len(kek) != _KEK_BYTES:
        raise ValueError("the job KEK must be 32 bytes")
    subkey = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt.encode("utf-8"),
        info=info.encode("utf-8"),
    ).derive(kek)
    return hmac.new(subkey, data, hashlib.sha256).hexdigest()[:hex_chars]


def seed_fingerprint(seed_b64: str) -> str:
    """Identifier of a caller-held synthesis seed: first 16 hex chars of sha256(raw seed)."""
    return hashlib.sha256(_b64decode(seed_b64)).hexdigest()[:16]
