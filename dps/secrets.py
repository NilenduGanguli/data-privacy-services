"""At-rest sealing for job secrets and inline connection credentials (contract §6.3).

``seal`` = ``b"\\x01" + nonce(12) + AES-256-GCM(kek, plaintext, aad)``. The AAD binds a blob to
its row (``"dps_job.key:<job id>"``, ``"dps_job.spec:<job id>"``, ``"dps_connection.secret:<id>"``),
so a sealed blob copied into another row does not open. This module and
``dps/engine/transform/crypto.py`` are the only modules that touch key bytes; neither logs, and
every error here is detail-free.

**Secret references are tenant-scoped** (security review SEC-2). A connection's ``secret_ref``
names a folder under the *caller's* tenant folder, ``DPS_SECRET_DIR/<tenant_id>/<ref>/``; the
tenant comes from the authenticated request, never from the reference. A reference whose first
segment names another top-level folder (another tenant's) is refused, and a symlink that leaves
the tenant's folder does not resolve. ``_shared/<ref>`` names ``DPS_SECRET_DIR/_shared/<ref>/``,
usable only by the tenants its ``tenants`` file lists (one id per line, ``#`` comments).
:func:`check_secret_ref` validates a reference when a connection is created or updated (no
secret is read); :func:`resolve_secret_ref` applies the same rules again when credentials load.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
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
    "ca.pem",
)
#: The top-level folder of secrets several tenants may use (``_`` never starts a tenant id).
SHARED_DIR = "_shared"
#: The allow-list file of a shared secret folder: one tenant id per line (never read as a secret).
SHARED_TENANTS_FILE = "tenants"
#: What ``ConnectionSpec.secret_ref`` accepts: a tenant-relative path, or ``_shared/<path>``.
SECRET_REF_PATTERN = r"^(?:_shared/)?[a-z0-9][a-z0-9/_.-]{0,200}$"
_SECRET_REF = re.compile(SECRET_REF_PATTERN)
#: A path segment of a reference: lower-case letters, digits, ``_ . -``, never starting with
#: ``.`` (hidden, ``..``) or ``_`` (reserved, like ``_shared``).
_SEGMENT = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
#: Tenant folder names: the job platform's tenant ids (``dps_tenant``, lower-case, no ``_`` start).
_TENANT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


class SealError(Exception):
    """A sealed blob could not be opened (wrong KEK, wrong row, tampered). Carries no detail."""

    def __init__(self) -> None:
        super().__init__("sealed blob could not be opened")


class SecretUnavailable(Exception):
    """A ``secret_ref`` does not resolve to a readable secret directory."""

    code = "secret_unavailable"

    def __init__(self) -> None:
        super().__init__("secret reference could not be resolved")


class SecretRefRefused(Exception):
    """A ``secret_ref`` the caller's tenant may not name (create/update time).

    ``code``: ``secret_ref_invalid`` (422, malformed), ``secret_ref_foreign_tenant`` (403, its
    first segment names another tenant's folder) or ``secret_ref_not_shared`` (403, a shared
    reference that does not list the tenant, or does not exist). ``detail`` never repeats the
    reference or a tenant id.
    """

    def __init__(self, code: str, status: int, detail: str) -> None:
        super().__init__(detail)
        self.code, self.status, self.detail = code, status, detail


def _invalid() -> SecretRefRefused:
    return SecretRefRefused(
        "secret_ref_invalid",
        422,
        "secret_ref must be a relative path of lower-case segments ([a-z0-9][a-z0-9_.-]*), "
        "optionally under _shared/",
    )


def _not_shared() -> SecretRefRefused:
    return SecretRefRefused(
        "secret_ref_not_shared", 403, "the shared secret does not list this tenant"
    )


def _inside(path: Path, base: Path) -> bool:
    return base in path.parents


def _shared_tenants(folder: Path, base: Path) -> set[str]:
    allow = folder / SHARED_TENANTS_FILE
    try:
        if not allow.is_file() or not _inside(allow.resolve(), base):
            return set()
        lines = allow.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    return {line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")}


def _locate(tenant_id: str, ref: str, settings: Settings) -> tuple[Path, Path]:
    """``(namespace root, secret folder)`` of ``ref`` for ``tenant_id`` (both resolved).

    The folder may not exist. Raises :class:`SecretRefRefused` for every rule in the module
    docstring; nothing here reads a secret file.
    """
    if not _TENANT.fullmatch(tenant_id or ""):
        raise _invalid()
    if not ref or not _SECRET_REF.fullmatch(ref):
        raise _invalid()
    segments = ref.split("/")
    shared = segments[0] == SHARED_DIR
    rest = segments[1:] if shared else segments
    if not rest or not all(_SEGMENT.fullmatch(seg) for seg in rest):
        raise _invalid()
    root = Path(settings.secret_dir).resolve()
    if shared:
        base = (root / SHARED_DIR).resolve()
        folder = base.joinpath(*rest).resolve()
        if not _inside(base, root) or not _inside(folder, base):
            raise _not_shared()
        if tenant_id not in _shared_tenants(folder, base):
            raise _not_shared()
        return base, folder
    first = rest[0]
    if first != tenant_id and (root / first).is_dir():
        raise SecretRefRefused(
            "secret_ref_foreign_tenant",
            403,
            "secret_ref is relative to this tenant's secret folder; its first segment names "
            "another tenant's folder",
        )
    base = (root / tenant_id).resolve()
    folder = base.joinpath(*rest).resolve()
    if not _inside(base, root) or not _inside(folder, base):
        raise SecretUnavailable()
    return base, folder


def check_secret_ref(tenant_id: str, ref: str, settings: Settings) -> None:
    """Validate ``ref`` for ``tenant_id`` without reading a secret (connection create/update).

    A tenant-scoped reference need not exist yet (it may be mounted later; loading it then fails
    with :class:`SecretUnavailable`); a shared one must list the tenant now.

    Raises:
        SecretRefRefused: the reference is malformed, names another tenant's folder, or is a
            shared reference that does not list the tenant.
    """
    try:
        _locate(tenant_id, ref, settings)
    except SecretUnavailable:
        raise _invalid() from None


def _b64decode(text: str) -> bytes:
    text = text.strip()
    pad = "=" * (-len(text) % 4)
    alt = b"-_" if ("-" in text or "_" in text) else None
    return base64.b64decode(text + pad, altchars=alt, validate=True)


#: KEK rotation (contract §6.3, S3 review F-21): the previous KEKs :func:`open_sealed` also tries
#: for a blob the current KEK does not open — registered by :func:`load_kek` from
#: ``DPS_JOB_KEK_PREVIOUS``, so every place that opens a blob (job keys and specs, discovery,
#: inline connection credentials) works during the rotation window, before ``dps.rekey`` ran.
_PREVIOUS_KEKS: dict[bytes, tuple[bytes, ...]] = {}


def load_kek(settings: Settings) -> bytes | None:
    """The 32-byte job KEK, or ``None`` when unset (settings already validated its length).

    A configured ``DPS_JOB_KEK_PREVIOUS`` is registered as the current KEK's fallback for
    :func:`open_sealed` (F-21)."""
    if not settings.job_kek:
        return None
    current, *previous = kek_ring(settings)
    if previous:
        _PREVIOUS_KEKS[current] = tuple(previous)
    else:
        _PREVIOUS_KEKS.pop(current, None)
    return current


def kek_ring(settings: Settings) -> tuple[bytes, ...]:
    """The job KEK, then ``DPS_JOB_KEK_PREVIOUS`` while a rotation keeps it (each set one)."""
    return tuple(_b64decode(v) for v in (settings.job_kek, settings.job_kek_previous) if v)


def seal(kek: bytes, plaintext: bytes, *, aad: str) -> bytes:
    """Encrypt and authenticate ``plaintext`` bound to ``aad`` (AES-256: the KEK is 32 bytes)."""
    if len(kek) != _KEK_BYTES:
        raise ValueError("the job KEK must be 32 bytes")
    nonce = os.urandom(_NONCE)
    return _VERSION + nonce + AESGCM(kek).encrypt(nonce, plaintext, aad.encode("utf-8"))


def _open_one(kek: bytes, sealed: bytes, aad: str) -> bytes:
    if len(sealed) < 1 + _NONCE + _TAG or sealed[:1] != _VERSION or len(kek) != _KEK_BYTES:
        raise SealError()
    try:
        return AESGCM(kek).decrypt(
            sealed[1 : 1 + _NONCE], sealed[1 + _NONCE :], aad.encode("utf-8")
        )
    except (InvalidTag, ValueError):
        raise SealError() from None


def open_sealed(kek: bytes, sealed: bytes, *, aad: str, fallback: bool = True) -> bytes:
    """Decrypt a blob from :func:`seal`; :class:`SealError` on any failure.

    During a KEK rotation the previous KEK registered for ``kek`` (:func:`load_kek`) is tried
    when ``kek`` does not open the blob (F-21); ``fallback=False`` opens with ``kek`` only (what
    ``dps.rekey`` uses to tell current blobs from those it must re-seal).
    """
    try:
        return _open_one(kek, sealed, aad)
    except SealError:
        if not fallback:
            raise
    for previous in _PREVIOUS_KEKS.get(kek, ()):
        try:
            return _open_one(previous, sealed, aad)
        except SealError:
            continue
    raise SealError()


def seal_json(kek: bytes, model: BaseModel, *, aad: str) -> bytes:
    """Seal a pydantic model's JSON (``SecretStr`` fields must be revealed by the caller first)."""
    return seal(kek, model.model_dump_json().encode("utf-8"), aad=aad)


def open_json[M: BaseModel](kek: bytes, sealed: bytes, model: type[M], *, aad: str) -> M:
    """Open and validate a sealed model; :class:`SealError` on any failure."""
    try:
        return model.model_validate_json(open_sealed(kek, sealed, aad=aad))
    except ValidationError:
        raise SealError() from None


def resolve_secret_ref(tenant_id: str, ref: str, settings: Settings) -> dict[str, str]:
    """Read the allow-listed files under ``DPS_SECRET_DIR/<tenant_id>/<ref>/`` (or an
    allow-listed ``_shared/<ref>/``), trailing newline stripped.

    Raises:
        SecretUnavailable: any rule of :func:`check_secret_ref` fails, the folder is missing, a
            file resolves outside the tenant's (or the shared) folder, or no file is readable.
    """
    try:
        base, folder = _locate(tenant_id, ref, settings)
    except SecretRefRefused:
        raise SecretUnavailable() from None
    if not folder.is_dir():
        raise SecretUnavailable()
    out: dict[str, str] = {}
    for name in SECRET_FILES:
        path = folder / name
        if path.is_file():
            if not _inside(path.resolve(), base):
                raise SecretUnavailable()
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
