"""Where the console browser checks write their screenshots (A-N2).

Screenshots of a check run are build output, not source: by default they go under the git-ignored
``reports/console/<name>/``, so a gate run never rewrites a committed file. ``DPS_SHOTS_DIR`` moves
the root (a relative path is read from the repository root), for example
``DPS_SHOTS_DIR=docs/superpowers/build`` to refresh the committed ``m1-console`` or ``m3-shots``
folders on purpose.

Used by ``tools/console_check.py`` (``m1-console``), ``tests/console/test_console_m2.py``
(``m2-console``) and ``tests/console/m3_console.py`` (``m3-shots``).
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
#: The default root: ``reports/`` is git-ignored (flow reports, demo screens, console shots).
DEFAULT_ROOT = ROOT / "reports" / "console"


def shots_dir(name: str) -> Path:
    """The screenshot folder of one console check: ``<root>/<name>`` (not created here)."""
    raw = os.environ.get("DPS_SHOTS_DIR", "").strip()
    root = Path(raw).expanduser() if raw else DEFAULT_ROOT
    if not root.is_absolute():
        root = ROOT / root
    return root / name
