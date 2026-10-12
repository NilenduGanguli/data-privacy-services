from __future__ import annotations

import base64

import pytest

from dps.config import Settings

KEK = base64.b64encode(bytes(range(32))).decode()


def test_m2_defaults_keep_l1_database_free() -> None:
    s = Settings(_env_file=None)
    assert s.pg_host == "" and s.pg_port == 5432 and s.pg_database == "dps" and s.pg_schema == "dps"
    assert s.pg_user == "dps_app" and s.migrations_mode == "auto" and s.rls_enabled is True
    assert (s.job_lease_seconds, s.partition_lease_seconds) == (300, 120)
    assert (s.job_max_attempts, s.partition_max_attempts) == (3, 5)
    assert (s.batch_rows, s.partition_rows, s.max_partitions, s.discovery_sample_rows) == (
        1000,
        50000,
        256,
        2000,
    )
    assert s.worker_metrics_port == 9400 and s.embedded_worker is False
    assert s.artefact_store == "local" and s.s3_bucket == "dps-artefacts"
    assert s.s3_force_path_style is True
    assert s.retention_days == 30 and s.audit_retention_months == 13 and s.audit_strict is False


def test_worker_and_migration_logins_fall_back_to_pg_user() -> None:
    s = Settings(_env_file=None, pg_user="u", pg_password="p")
    assert s.pg_worker_login == ("u", "p") and s.pg_migration_login == ("u", "p")
    s2 = Settings(
        _env_file=None,
        pg_worker_user="w",
        pg_worker_password="wp",
        pg_migration_user="o",
        pg_migration_password="op",
    )
    assert s2.pg_worker_login == ("w", "wp") and s2.pg_migration_login == ("o", "op")


@pytest.mark.parametrize(
    "field,value",
    [
        ("pg_schema", "Bad-Schema"),
        ("pg_schema", "x" * 64),
        ("migrations_mode", "always"),
        ("artefact_store", "gcs"),
    ],
)
def test_invalid_m2_values_are_refused(field: str, value: str) -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{field: value})


def test_kek_must_decode_to_32_bytes_and_errors_never_echo_it() -> None:
    assert Settings(_env_file=None, job_kek=KEK).job_kek == KEK
    short = base64.b64encode(b"tooshort-secret-material").decode()
    with pytest.raises(ValueError) as err:
        Settings(_env_file=None, job_kek=short)
    assert short not in str(err.value) and "32 bytes" in str(err.value)


def test_kek_from_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "kek"
    path.write_text(KEK + "\n", encoding="utf-8")
    monkeypatch.delenv("DPS_JOB_KEK", raising=False)
    monkeypatch.setenv("DPS_JOB_KEK_FILE", str(path))
    assert Settings(_env_file=None).job_kek == KEK
