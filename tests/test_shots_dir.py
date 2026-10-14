"""Console browser checks write their screenshots to an ignored folder (A-N2).

A gate run used to rewrite committed screenshots (``tools/console_check.py`` into
``docs/superpowers/build/m1-console/``, the M3 console checks into ``docs/superpowers/build/m3-shots/``),
leaving dozens of modified PNGs in every worktree that ran the tests. Screenshots of a check run are
build output: by default they go under the git-ignored ``reports/console/<name>/``;
``DPS_SHOTS_DIR`` moves them on purpose (e.g. to refresh the committed folders).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.shots import shots_dir

ROOT = Path(__file__).resolve().parents[1]
HARNESSES = (
    "tools/console_check.py",
    "tests/console/m3_console.py",
    "tests/console/test_console_m2.py",
)
NAMES = ("m1-console", "m2-console", "m3-shots")


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    if shutil.which("git") is None:  # pragma: no cover - every dev checkout has git
        pytest.skip("git is not installed")
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)


def test_screenshots_default_to_the_ignored_reports_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DPS_SHOTS_DIR", raising=False)
    for name in NAMES:
        folder = shots_dir(name)
        assert folder == ROOT / "reports" / "console" / name
        rel = (folder / "01-page-desktop.png").relative_to(ROOT).as_posix()
        assert _git("check-ignore", "-q", rel).returncode == 0, f"{rel} is not git-ignored"
        assert _git("ls-files", "--", folder.relative_to(ROOT).as_posix()).stdout == ""


def test_shots_dir_moves_on_purpose(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DPS_SHOTS_DIR", "docs/superpowers/build")
    assert shots_dir("m3-shots") == ROOT / "docs" / "superpowers" / "build" / "m3-shots"
    monkeypatch.setenv("DPS_SHOTS_DIR", str(tmp_path))
    assert shots_dir("m1-console") == tmp_path / "m1-console"


def test_every_console_harness_takes_its_folder_from_shots_dir() -> None:
    tracked_dir = re.compile(r'"docs"\s*/\s*"superpowers"\s*/\s*"build"')
    for rel in HARNESSES:
        source = (ROOT / rel).read_text(encoding="utf-8")
        assert "shots_dir(" in source, rel
        assert not tracked_dir.search(source), f"{rel} still writes under docs/superpowers/build"


def test_the_ignore_rule_hides_no_tracked_source() -> None:
    # `reports/` unanchored would also hide dps/layers/reports/ (the evidence PDF code).
    assert _git("ls-files", "-i", "-c", "--exclude-standard").stdout == ""
    assert _git("check-ignore", "-q", "dps/layers/reports/pdf.py").returncode == 1
