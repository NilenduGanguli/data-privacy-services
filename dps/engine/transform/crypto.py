"""Key material and the primitives every reversible method is built from (spec §5.5).

* A **DEK** (256-bit) is generated per request and returned to the caller; DPS never stores it.
* **Subkeys** come from HKDF-SHA256 with the key id as salt and ``dps.v1|<method>|<entity>`` as
  info, so a leaked subkey compromises one method/type pair only.
* **Deterministic encryption** is AES-SIV (RFC 5297) with the entity type as associated data —
  the same value under the same key always yields the same token, which is what gives joins and
  cross-system consistency.
* The DEK may be **wrapped** with the caller's RSA public key (OAEP-SHA256) so it never crosses the
  wire in clear.

This module logs nothing and has no debug paths. Keep it that way. Error messages name the rule
that failed, never a key, a token or a value.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESSIV
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import BaseModel, ConfigDict, Field

ALG = "dps.v1"
DEK_BYTES = 32
SUBKEY_BYTES = 32
#: AES-SIV takes two AES keys; 64 bytes gives AES-256 for both halves (S2V and CTR).
SIV_KEY_BYTES = 64
#: Smallest RSA modulus accepted for wrapping the DEK (NIST SP 800-57: 2048 bits through 2030).
MIN_RSA_BITS = 2048

_B64U = re.compile(r"[A-Za-z0-9_-]*")
_B32 = re.compile(r"[a-z2-7]*")


def b64u(data: bytes) -> str:
    """Unpadded base64url."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64u(text: str) -> bytes:
    """Strict inverse of :func:`b64u`; ``ValueError`` on any character outside base64url."""
    if not _B64U.fullmatch(text) or len(text) % 4 == 1:
        raise ValueError("not unpadded base64url")
    try:
        return base64.urlsafe_b64decode(text + "=" * ((-len(text)) % 4))
    except binascii.Error as exc:
        raise ValueError("not unpadded base64url") from exc


def b32(data: bytes) -> str:
    """Lower-case, unpadded base32 — the token alphabet (safe in URLs, SQL and file names)."""
    return base64.b32encode(data).decode("ascii").lower().rstrip("=")


def unb32(text: str) -> bytes:
    """Strict inverse of :func:`b32`; ``ValueError`` on anything else."""
    if not _B32.fullmatch(text):
        raise ValueError("not lower-case base32")
    padded = text.upper() + "=" * ((-len(text)) % 8)
    try:
        return base64.b32decode(padded)
    except binascii.Error as exc:
        raise ValueError("not lower-case base32") from exc


class KeyMaterial(BaseModel):
    """What the caller receives (and later presents) to reveal or to stay consistent.

    Validation errors never echo the input (it carries the DEK), and ``repr`` hides the DEK.
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    alg: str = ALG
    kid: str
    dek: str | None = Field(default=None, repr=False)
    wrapped_dek: str | None = None
    wrap: dict[str, str] | None = None
    consistency_scope: str = "request"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def has_dek(self) -> bool:
        """True when the plaintext DEK is present (not wrapped-only)."""
        return self.dek is not None


def new_kid() -> str:
    """A random key id (``k_`` + 16 hex characters); it is the HKDF salt, not a secret."""
    return "k_" + secrets.token_hex(8)


def generate_key_material(scope: str = "request") -> KeyMaterial:
    """A fresh 256-bit DEK with a random key id."""
    return KeyMaterial(
        kid=new_kid(), dek=b64u(secrets.token_bytes(DEK_BYTES)), consistency_scope=scope
    )


def dek_bytes(km: KeyMaterial) -> bytes:
    """The raw DEK; ``KeyError`` when the material is wrapped-only, ``ValueError`` if malformed."""
    if km.dek is None:
        raise KeyError("key material carries no plaintext dek (wrapped only)")
    raw = unb64u(km.dek)
    if len(raw) != DEK_BYTES:
        raise ValueError("dek must be 32 bytes")
    return raw


def key_material_from_caller(payload: dict[str, Any]) -> KeyMaterial:
    """Validate caller-presented key material (reveal, or consistency scope ``key``).

    Raises:
        ValueError: wrong ``alg``, no plaintext DEK, or a DEK that is not 32 bytes of base64url.
            The message never contains the DEK.
    """
    km = KeyMaterial.model_validate(payload)
    if km.alg != ALG:
        raise ValueError(f"unsupported key material alg; expected {ALG!r}")
    if not km.has_dek():
        raise ValueError("key material must carry the plaintext dek; unwrap it client-side first")
    if not km.kid:
        raise ValueError("key material must carry a kid")
    dek_bytes(km)  # format and length check
    return km


def subkey(km: KeyMaterial, info: str, length: int = SUBKEY_BYTES) -> bytes:
    """HKDF-SHA256 subkey for ``info`` (e.g. ``"det_token|EMAIL"``), salted by the key id.

    A length other than :data:`SUBKEY_BYTES` is bound into the HKDF info, because HKDF output of
    one length is a prefix of the output of a longer one for the same info.
    """
    suffix = "" if length == SUBKEY_BYTES else f"|L{length}"
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=km.kid.encode("utf-8"),
        info=f"{ALG}|{info}{suffix}".encode(),
    )
    return hkdf.derive(dek_bytes(km))


def det_encrypt(key: bytes, plaintext: bytes, aad: str) -> bytes:
    """AES-SIV deterministic authenticated encryption bound to ``aad``."""
    return AESSIV(key).encrypt(plaintext, [aad.encode("utf-8")])


def det_decrypt(key: bytes, ciphertext: bytes, aad: str) -> bytes:
    """Inverse of :func:`det_encrypt`; raises ``cryptography.exceptions.InvalidTag`` on mismatch."""
    return AESSIV(key).decrypt(ciphertext, [aad.encode("utf-8")])


def hmac_bytes(key: bytes, data: str) -> bytes:
    """HMAC-SHA256 of ``data``."""
    return hmac.new(key, data.encode("utf-8"), hashlib.sha256).digest()


def derive_int(key: bytes, data: str, modulus: int) -> int:
    """A stable integer in ``[0, modulus)`` from ``HMAC(key, data)``."""
    if modulus <= 0:
        raise ValueError("modulus must be positive")
    return int.from_bytes(hmac_bytes(key, data)[:8], "big") % modulus


def _oaep() -> padding.OAEP:
    return padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None
    )


def wrap_key_material(km: KeyMaterial, public_key_pem: str) -> KeyMaterial:
    """Return a copy whose DEK is RSA-OAEP-256 wrapped with the caller's public key.

    Raises:
        ValueError: the PEM is unreadable, not RSA, or shorter than :data:`MIN_RSA_BITS`.
    """
    try:
        public = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
    except (ValueError, TypeError, UnsupportedAlgorithm) as exc:
        raise ValueError("wrap_key public_key_pem is not a readable PEM public key") from exc
    if not isinstance(public, rsa.RSAPublicKey):
        raise ValueError("wrap_key requires an RSA public key (PEM, SubjectPublicKeyInfo)")
    if public.key_size < MIN_RSA_BITS:
        raise ValueError(f"wrap_key requires an RSA key of at least {MIN_RSA_BITS} bits")
    spki = public.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    wrapped = public.encrypt(dek_bytes(km), _oaep())
    return km.model_copy(
        update={
            "dek": None,
            "wrapped_dek": base64.b64encode(wrapped).decode("ascii"),
            "wrap": {"type": "rsa-oaep-256", "fingerprint": hashlib.sha256(spki).hexdigest()},
        }
    )


def unwrap_dek(wrapped_dek_b64: str, private_key_pem: str, password: bytes | None = None) -> bytes:
    """Client-side helper (and test oracle): recover the DEK with the matching private key."""
    private = serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=password)
    if not isinstance(private, rsa.RSAPrivateKey):
        raise ValueError("unwrap requires an RSA private key")
    return private.decrypt(base64.b64decode(wrapped_dek_b64), _oaep())
