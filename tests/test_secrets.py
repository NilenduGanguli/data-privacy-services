from __future__ import annotations

import base64
import os

import pytest
from pydantic import BaseModel

from dps.config import Settings
from dps.secrets import (
    SealError,
    SecretUnavailable,
    load_kek,
    open_json,
    open_sealed,
    resolve_secret_ref,
    seal,
    seal_json,
    seed_fingerprint,
)

KEK = os.urandom(32)


class Box(BaseModel):
    dek: str


def test_seal_round_trip_and_format() -> None:
    sealed = seal(KEK, b"payload", aad="dps_job.key:j_1")
    assert sealed[:1] == b"\x01" and len(sealed) == 1 + 12 + len(b"payload") + 16
    assert open_sealed(KEK, sealed, aad="dps_job.key:j_1") == b"payload"
    assert seal(KEK, b"payload", aad="a") != seal(KEK, b"payload", aad="a")  # fresh nonce


@pytest.mark.parametrize("tamper", ["aad", "kek", "bit", "version", "short"])
def test_open_fails_closed_without_detail(tamper: str) -> None:
    sealed = seal(KEK, b"secret-value", aad="dps_job.key:j_1")
    kek, aad, blob = KEK, "dps_job.key:j_1", bytearray(sealed)
    if tamper == "aad":
        aad = "dps_job.key:j_2"  # a blob moved to another row
    elif tamper == "kek":
        kek = os.urandom(32)
    elif tamper == "bit":
        blob[-1] ^= 1
    elif tamper == "version":
        blob[0] = 2
    else:
        blob = blob[:10]
    with pytest.raises(SealError) as err:
        open_sealed(kek, bytes(blob), aad=aad)
    assert str(err.value) == "sealed blob could not be opened"


def test_seal_json_models() -> None:
    blob = seal_json(KEK, Box(dek="k"), aad="x:1")
    assert open_json(KEK, blob, Box, aad="x:1").dek == "k"


def test_load_kek() -> None:
    assert load_kek(Settings(_env_file=None)) is None
    assert load_kek(Settings(_env_file=None, job_kek=base64.b64encode(KEK).decode())) == KEK


def test_resolve_secret_ref(tmp_path) -> None:  # noqa: ANN001
    d = tmp_path / "dps" / "src" / "bank-pg"
    d.mkdir(parents=True)
    (d / "username").write_text("dps_reader\n")
    (d / "password").write_text("pw\n")
    (d / "notes.txt").write_text("ignored")
    s = Settings(_env_file=None, secret_dir=str(tmp_path))
    assert resolve_secret_ref("dps/src/bank-pg", s) == {"username": "dps_reader", "password": "pw"}
    for bad in ("../etc", "/abs", "dps/../../x", "dps/src/missing"):
        with pytest.raises(SecretUnavailable) as err:
            resolve_secret_ref(bad, s)
        assert err.value.code == "secret_unavailable" and "pw" not in str(err.value)


def test_seed_fingerprint_is_stable_and_short() -> None:
    seed = base64.urlsafe_b64encode(bytes(32)).decode().rstrip("=")
    assert seed_fingerprint(seed) == seed_fingerprint(seed) and len(seed_fingerprint(seed)) == 16


def test_seal_refuses_a_kek_that_is_not_32_bytes() -> None:
    with pytest.raises(ValueError) as err:
        seal(KEK[:16], b"payload", aad="x:1")  # AES-128 would silently weaken the sealing
    assert "32 bytes" in str(err.value)
