"""HANDOFF.md stays complete and current (M5 Task 27, Definition of Done 11).

The configuration table is generated from ``dps.config.Settings`` by ``tools/settings_table.py``;
every setting, compose service, OpenShift role and mounted secret must be named in HANDOFF, and
§5 must name every role that reads each secret (what each role opens at start, and every workload
the manifests mount it in).
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import sys
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import pytest
import yaml

from dps import db, rekey, worker
from dps.config import Settings
from dps.posture import SECRET_ENV, PostureError

ROOT = Path(__file__).resolve().parents[1]
HANDOFF = (ROOT / "HANDOFF.md").read_text(encoding="utf-8")
sys.path.insert(0, str(ROOT / "tools"))

import settings_table  # noqa: E402


def _section(number: str) -> str:
    """HANDOFF section ``number`` (``"5"``, ``"3.3"``) up to the next numbered heading of the
    same or a higher level."""
    depth = number.count(".") + 2
    lines = HANDOFF.splitlines()
    head = re.compile(rf"#{{{depth}}} {re.escape(number)}\.? ")
    start = next(i for i, line in enumerate(lines) if head.match(line))
    stop = re.compile(rf"#{{2,{depth}}} \d")
    end = next((j for j in range(start + 1, len(lines)) if stop.match(lines[j])), len(lines))
    return "\n".join(lines[start:end])


def _secret_rows() -> dict[str, tuple[str, str]]:
    """§5's mounted Secrets: name -> (what it feeds, who reads it)."""
    rows = {}
    for line in _section("5").splitlines():
        if line.startswith("| Secret `"):
            name, feeds, readers = (cell.strip() for cell in line.strip().strip("|").split("|"))
            rows[name.split("`")[1]] = (feeds, readers)
    return rows


def _reads(readers: str, role: str) -> bool:
    """Whether a "Read by" cell names ``role`` (``dps-worker`` or ``worker``; the
    ``dps-browser-worker`` is another role)."""
    short = role.removeprefix("dps-")
    return re.search(rf"(?<![\w.-])(?:dps-)?{re.escape(short)}(?![\w-])", readers) is not None


def test_settings_table_is_current() -> None:
    out = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "settings_table.py"), "--check"],
        capture_output=True,
        check=False,
    )
    assert out.returncode == 0, out.stdout.decode()


def test_every_setting_is_documented() -> None:
    assert set(settings_table.FIELD_DOCS) == set(Settings.model_fields)
    assert all(f"DPS_{name.upper()}" in HANDOFF for name in Settings.model_fields)


def test_handoff_names_every_compose_service_and_openshift_role() -> None:
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]
    for name in [*services, "dps-app", "dps-worker", "dps-browser-worker", "dps-migrate"]:
        assert name in HANDOFF, name


def test_handoff_lists_every_secret_the_manifests_mount() -> None:
    base = ROOT / "deploy" / "openshift" / "base"
    if not base.is_dir():  # M5 Task 25 (OpenShift) lands on its own branch; this runs once merged
        pytest.skip("deploy/openshift/base is not on this branch yet (M5 Task 25)")
    names = set()
    for path in base.glob("*.yaml"):
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            text = yaml.safe_dump(doc or {})
            names |= {
                line.split(":", 1)[1].strip() for line in text.splitlines() if "secretName:" in line
            }
    assert names and all(n in HANDOFF for n in names), sorted(n for n in names if n not in HANDOFF)


def test_handoff_names_every_workload_that_mounts_each_secret() -> None:
    base = ROOT / "deploy" / "openshift" / "base"
    if not base.is_dir():  # M5 Task 25 (OpenShift) lands on its own branch; this runs once merged
        pytest.skip("deploy/openshift/base is not on this branch yet (M5 Task 25)")
    rows = _secret_rows()
    missing = []
    for path in sorted(base.glob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            kind = (doc or {}).get("kind")
            if kind not in {"Deployment", "Job", "CronJob"}:
                continue
            spec = doc["spec"]["jobTemplate"]["spec"] if kind == "CronJob" else doc["spec"]
            # the migrate Job is renamed per release (dps-migrate-<release>)
            role = "dps-migrate" if kind == "Job" else doc["metadata"]["name"]
            for volume in spec["template"]["spec"].get("volumes", []):
                secret = (volume.get("secret") or {}).get("secretName")
                if secret and not _reads(rows.get(secret, ("", ""))[1], role):
                    missing.append(f"{secret} <- {role}")
    assert not missing, missing


# --- §5 "Read by" and §3.3 follow what each role does at start (S7 review r1, B1) ---------------
class _Opened(Exception):
    """Stops a role's start right after it opened its database pools."""


def _pools_opened(
    monkeypatch: pytest.MonkeyPatch, start: Callable[[], Coroutine[Any, Any, Any]]
) -> list[str]:
    opened: list[str] = []

    async def app_pool(settings: Settings) -> None:
        opened.append("app login")

    async def worker_pool(settings: Settings) -> None:
        opened.append("worker login")
        raise _Opened

    monkeypatch.setattr(db, "init_pool", app_pool)
    monkeypatch.setattr(db, "init_worker_pool", worker_pool)
    with pytest.raises(_Opened):
        asyncio.run(start())
    return opened


def test_secret_rows_name_every_role_that_logs_in_with_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None, pg_host="db.internal")  # type: ignore[call-arg]
    both = ["app login", "worker login"]
    assert _pools_opened(monkeypatch, lambda: worker._amain(settings)) == both
    assert _pools_opened(monkeypatch, lambda: rekey.amain([], settings=settings)) == both
    rows = _secret_rows()
    app_login, worker_login = rows["dps-pg-app"][1], rows["dps-pg-worker"][1]
    assert _reads(app_login, "dps-app") and _reads(app_login, "dps-worker"), app_login
    assert "dps.rekey" in app_login, app_login
    assert _reads(worker_login, "dps-worker") and "dps.rekey" in worker_login, worker_login


def test_api_keys_secret_is_required_at_boot_by_the_app_and_the_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prod = Settings(_env_file=None, env="prod", api_keys="")  # type: ignore[call-arg]
    monkeypatch.setattr(worker, "get_settings", lambda: prod)
    monkeypatch.setattr(worker, "configure_logging", lambda *a, **k: None)
    with pytest.raises(PostureError, match="DPS_API_KEYS"):
        worker.main()  # the worker boots through the app's posture guard
    feeds, readers = _secret_rows()["dps-api-keys"]
    assert _reads(readers, "dps-app") and _reads(readers, "dps-worker"), readers
    words = f"{feeds} {readers}".lower()
    assert "optional" not in words and "required at boot" in words, feeds
    assert "python -m dps.worker" in " ".join(_section("3.3").split())


# --- the tool itself ---------------------------------------------------------------------------
def test_an_undocumented_setting_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    docs = dict(settings_table.FIELD_DOCS)
    docs.pop("tokenizer_url")
    docs["no_such_setting"] = ("M9", "stale")
    monkeypatch.setattr(settings_table, "FIELD_DOCS", docs)
    assert settings_table.problems() == [
        "undocumented setting DPS_TOKENIZER_URL (add it to FIELD_DOCS)",
        "FIELD_DOCS names DPS_NO_SUCH_SETTING, which Settings no longer has",
    ]
    assert settings_table.main(["--check"]) == 1


def test_check_fails_on_a_stale_table_and_the_rewrite_fixes_it(tmp_path: Path) -> None:
    handoff = tmp_path / "HANDOFF.md"
    stale = settings_table.render().replace("| M5 |", "| M4 |", 1)
    handoff.write_text(f"# HANDOFF\n\nbefore\n\n{stale}\n\nafter\n", encoding="utf-8")
    assert settings_table.main(["--check", "--handoff", str(handoff)]) == 1
    assert settings_table.main(["--handoff", str(handoff)]) == 0
    assert settings_table.main(["--check", "--handoff", str(handoff)]) == 0
    text = handoff.read_text(encoding="utf-8")
    assert text.startswith("# HANDOFF\n\nbefore\n\n") and text.endswith("\n\nafter\n")
    missing = tmp_path / "NONE.md"
    missing.write_text("# no markers\n", encoding="utf-8")
    assert settings_table.main(["--check", "--handoff", str(missing)]) == 1


def test_secret_rows_never_show_a_default() -> None:
    rows = {
        line.split("|")[1].strip(" `"): line
        for line in settings_table.render().splitlines()
        if line.startswith("| `DPS_")
    }
    assert len(rows) == len(Settings.model_fields)
    for env in [*SECRET_ENV, "DPS_BOOTSTRAP_API_KEY"]:
        cells = [c.strip() for c in rows[env].split("|")]
        assert cells[2] == settings_table.DASH and cells[3] == "secret (`_FILE`)", env
    tag = [c.strip() for c in rows["DPS_LLM_EGRESS_CHECK"].split("|")]
    assert tag[2] == "`strict`" and tag[3] == ""
