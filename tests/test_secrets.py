from __future__ import annotations

import base64
import os

import pytest
from pydantic import BaseModel

from dps.config import Settings
from dps.secrets import (
    SealError,
    SecretRefRefused,
    SecretUnavailable,
    check_secret_ref,
    keyed_fingerprint,
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


def _secret(root, tenant: str, ref: str, user: str = "dps_reader", pw: str = "pw") -> None:  # noqa: ANN001
    d = root / tenant / ref
    d.mkdir(parents=True)
    (d / "username").write_text(user + "\n")
    (d / "password").write_text(pw + "\n")


def test_resolve_secret_ref_reads_only_the_tenants_own_folder(tmp_path) -> None:  # noqa: ANN001
    _secret(tmp_path, "t1", "dps/src/bank-pg")
    (tmp_path / "t1" / "dps" / "src" / "bank-pg" / "notes.txt").write_text("ignored")
    s = Settings(_env_file=None, secret_dir=str(tmp_path))
    assert resolve_secret_ref("t1", "dps/src/bank-pg", s) == {
        "username": "dps_reader",
        "password": "pw",
    }
    # the same name under another tenant is that tenant's (missing) secret, never t1's
    with pytest.raises(SecretUnavailable):
        resolve_secret_ref("t2", "dps/src/bank-pg", s)
    for bad in ("../etc", "/abs", "dps/../../x", "dps/src/missing", "t1/../t1/dps/src/bank-pg"):
        with pytest.raises(SecretUnavailable) as err:
            resolve_secret_ref("t1", bad, s)
        assert err.value.code == "secret_unavailable" and "pw" not in str(err.value)


def test_a_ref_naming_another_tenants_folder_is_refused_and_never_resolves(tmp_path) -> None:  # noqa: ANN001
    _secret(tmp_path, "t1", "dps/src/bank-pg")
    (tmp_path / "t2").mkdir()
    s = Settings(_env_file=None, secret_dir=str(tmp_path))
    with pytest.raises(SecretRefRefused) as err:
        check_secret_ref("t2", "t1/dps/src/bank-pg", s)
    assert (err.value.code, err.value.status) == ("secret_ref_foreign_tenant", 403)
    assert "bank-pg" not in str(err.value) and "t1" not in err.value.detail
    with pytest.raises(SecretUnavailable):  # defence in depth: load refuses it too
        resolve_secret_ref("t2", "t1/dps/src/bank-pg", s)
    check_secret_ref("t2", "dps/src/bank-pg", s)  # its own (missing) secret: allowed, not read
    check_secret_ref("t1", "dps/src/bank-pg", s)


@pytest.mark.parametrize(
    "ref",
    [
        "",
        "/abs",
        "dps/../x",
        "dps//x",
        "dps/./x",
        "dps/.hidden",
        "dps/_x",
        "Dps/x",
        "_other/x",
        "_shared",
        "_shared/",
        "dps\\x",
    ],
)
def test_malformed_refs_are_refused_at_create_and_load(tmp_path, ref: str) -> None:  # noqa: ANN001
    s = Settings(_env_file=None, secret_dir=str(tmp_path))
    with pytest.raises(SecretRefRefused) as err:
        check_secret_ref("t1", ref, s)
    assert (err.value.code, err.value.status) == ("secret_ref_invalid", 422)
    with pytest.raises(SecretUnavailable):
        resolve_secret_ref("t1", ref, s)


def test_shared_refs_resolve_only_for_allow_listed_tenants(tmp_path) -> None:  # noqa: ANN001
    _secret(tmp_path, "_shared", "s3/exports", user="key-id", pw="key-secret")
    folder = tmp_path / "_shared" / "s3" / "exports"
    s = Settings(_env_file=None, secret_dir=str(tmp_path))
    for tenant in ("t1", "t2"):  # no tenants file: nobody may use it
        with pytest.raises(SecretRefRefused) as err:
            check_secret_ref(tenant, "_shared/s3/exports", s)
        assert (err.value.code, err.value.status) == ("secret_ref_not_shared", 403)
        with pytest.raises(SecretUnavailable):
            resolve_secret_ref(tenant, "_shared/s3/exports", s)
    (folder / "tenants").write_text("# tenants that may use this secret\nt1\n\n")
    check_secret_ref("t1", "_shared/s3/exports", s)
    files = resolve_secret_ref("t1", "_shared/s3/exports", s)
    assert files == {"username": "key-id", "password": "key-secret"}  # the list is not a secret
    with pytest.raises(SecretRefRefused):
        check_secret_ref("t2", "_shared/s3/exports", s)
    with pytest.raises(SecretUnavailable):
        resolve_secret_ref("t2", "_shared/s3/exports", s)
    # a missing shared folder answers exactly like one the tenant is not listed in
    with pytest.raises(SecretRefRefused) as err:
        check_secret_ref("t1", "_shared/s3/none", s)
    assert err.value.code == "secret_ref_not_shared"
    # a tenant-scoped ref never reaches the shared folder
    with pytest.raises(SecretUnavailable):
        resolve_secret_ref("t1", "s3/exports", s)


def test_symlinks_out_of_the_tenants_folder_are_refused(tmp_path) -> None:  # noqa: ANN001
    _secret(tmp_path, "t1", "dps/src/bank-pg")
    (tmp_path / "t2" / "dps" / "src").mkdir(parents=True)
    (tmp_path / "t2" / "dps" / "src" / "stolen").symlink_to(
        tmp_path / "t1" / "dps" / "src" / "bank-pg"
    )
    loose = tmp_path / "t2" / "dps" / "src" / "loose"
    loose.mkdir()
    (loose / "username").write_text("u")
    (loose / "password").symlink_to(tmp_path / "t1" / "dps" / "src" / "bank-pg" / "password")
    s = Settings(_env_file=None, secret_dir=str(tmp_path))
    with pytest.raises(SecretUnavailable):
        resolve_secret_ref("t2", "dps/src/stolen", s)
    with pytest.raises(SecretUnavailable):
        resolve_secret_ref("t2", "dps/src/loose", s)
    # a symlink inside the tenant's own folder (the Kubernetes ..data layout) still resolves
    real = tmp_path / "t2" / "dps" / "src" / "k8s" / "..2026_10_12"
    real.mkdir(parents=True)
    (real / "username").write_text("kube")
    (real / "password").write_text("kube-pw")
    (tmp_path / "t2" / "dps" / "src" / "k8s" / "..data").symlink_to(real)
    for name in ("username", "password"):
        (tmp_path / "t2" / "dps" / "src" / "k8s" / name).symlink_to(f"..data/{name}")
    assert resolve_secret_ref("t2", "dps/src/k8s", s) == {"username": "kube", "password": "kube-pw"}


def test_a_tenant_id_that_is_not_a_folder_name_never_resolves(tmp_path) -> None:  # noqa: ANN001
    _secret(tmp_path, "t1", "dps/src/bank-pg")
    s = Settings(_env_file=None, secret_dir=str(tmp_path))
    for tenant in ("", "_system", "..", "T1", "t1/..", "_shared"):
        with pytest.raises(SecretUnavailable):
            resolve_secret_ref(tenant, "dps/src/bank-pg", s)
        with pytest.raises(SecretRefRefused):
            check_secret_ref(tenant, "dps/src/bank-pg", s)


def test_seed_fingerprint_is_stable_and_short() -> None:
    seed = base64.urlsafe_b64encode(bytes(32)).decode().rstrip("=")
    assert seed_fingerprint(seed) == seed_fingerprint(seed) and len(seed_fingerprint(seed)) == 16


def test_seal_refuses_a_kek_that_is_not_32_bytes() -> None:
    with pytest.raises(ValueError) as err:
        seal(KEK[:16], b"payload", aad="x:1")  # AES-128 would silently weaken the sealing
    assert "32 bytes" in str(err.value)


def test_keyed_fingerprint_depends_on_kek_salt_info_and_data() -> None:
    base = keyed_fingerprint(KEK, info="dps.checkpoint_fp", salt="j_1", data=b"k")
    assert len(base) == 8 and int(base, 16) >= 0
    assert keyed_fingerprint(KEK, info="dps.checkpoint_fp", salt="j_1", data=b"k") == base
    for variant in (
        keyed_fingerprint(os.urandom(32), info="dps.checkpoint_fp", salt="j_1", data=b"k"),
        keyed_fingerprint(KEK, info="dps.other", salt="j_1", data=b"k"),
        keyed_fingerprint(KEK, info="dps.checkpoint_fp", salt="j_2", data=b"k"),
        keyed_fingerprint(KEK, info="dps.checkpoint_fp", salt="j_1", data=b"l"),
    ):
        assert variant != base
    assert len(keyed_fingerprint(KEK, info="i", salt="s", data=b"", hex_chars=16)) == 16
    with pytest.raises(ValueError):
        keyed_fingerprint(KEK[:16], info="i", salt="s", data=b"k")
