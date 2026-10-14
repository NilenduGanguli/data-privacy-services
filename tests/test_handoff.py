"""HANDOFF.md stays complete and current (M5 Task 27, Definition of Done 11).

The configuration table is generated from ``dps.config.Settings`` by ``tools/settings_table.py``;
every setting, compose service, OpenShift role and mounted secret must be named in HANDOFF.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from dps.config import Settings
from dps.posture import SECRET_ENV

ROOT = Path(__file__).resolve().parents[1]
HANDOFF = (ROOT / "HANDOFF.md").read_text(encoding="utf-8")
sys.path.insert(0, str(ROOT / "tools"))

import settings_table  # noqa: E402


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
