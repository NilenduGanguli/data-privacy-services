"""A built browser-worker image runs under an arbitrary UID in group 0 with a read-only root (M4's
image under M5 Task 24's rules).

Opt-in: ``DPS_TEST_BROWSER_IMAGE=data-privacy-services-browser:<tag> pytest -q
tests/test_browser_image_runtime.py`` (``DPS_TEST_IMAGE_PLATFORM=linux/amd64`` checks an amd64
build under emulation; with ``DPS_TEST_IMAGE=data-privacy-services:<tag>`` as well, the app image
reads what the browser image stored). Each check starts throw-away containers the way OpenShift's
restricted SCC does — a random UID whose only group is 0, ``--read-only`` with a ``/tmp`` tmpfs, no
capabilities, no privilege escalation — through the image's own entrypoint. Chromium is launched by
the scanner's own ``get_browser`` (its launch flags) on an inline page; nothing here needs the
stack or a network.

The browser-worker stores the scan screenshots on the artefact volume the app serves them from, so
whichever UID each of them runs as, an object one stored must stay readable by the other (measured
on branch ``m4``'s image: an arbitrary UID got ``HOME=/`` and ``umask 0022``, and the objects it
stored were ``0600``).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from tests.test_image_runtime import MACHINES, STORE_PROBE

IMAGE = os.environ.get("DPS_TEST_BROWSER_IMAGE", "")
APP_IMAGE = os.environ.get("DPS_TEST_IMAGE", "")
PLATFORM = os.environ.get("DPS_TEST_IMAGE_PLATFORM", "")
#: An OpenShift-style UID: high, absent from /etc/passwd, group 0.
UID = "1000770000"
#: Chromium 1187 is what playwright==1.55.0 drives; the base image bakes it under /ms-playwright.
CHROMIUM = "/ms-playwright/chromium-1187"

pytestmark = pytest.mark.skipif(
    not IMAGE or shutil.which("docker") is None,
    reason="set DPS_TEST_BROWSER_IMAGE=data-privacy-services-browser:<tag> (and have docker)",
)

PROBE = r"""
import asyncio, importlib, importlib.metadata, json, os, pathlib, platform

def writable(path):
    p = pathlib.Path(path) / ".dps-write-probe"
    try:
        p.write_text("x")
        p.unlink()
        return True
    except OSError:
        return False

def umask():
    for line in pathlib.Path("/proc/self/status").read_text().splitlines():
        if line.startswith("Umask:"):
            return line.split()[1]
    return None

mods = {}
for name in ("dps.browser_worker", "dps.layers.scanner", "playwright.async_api", "PIL", "fastfpe",
             "asyncpg", "sqlglot", "pyarrow", "boto3", "oracledb", "pymongo"):
    try:
        importlib.import_module(name)
        mods[name] = True
    except Exception as exc:  # noqa: BLE001
        mods[name] = type(exc).__name__

async def chromium():
    from dps.config import Settings
    from dps.layers.scanner.browser import close_browser, get_browser
    try:
        browser = await get_browser(Settings(_env_file=None))
        page = await browser.new_page()
        await page.set_content("<p id='x'>chromium ok</p>")
        text = await page.inner_text("#x")
        png = await page.screenshot()
        return {"text": text, "png": png[:4] == b"\x89PNG", "version": browser.version}
    except Exception as exc:  # noqa: BLE001
        return {"error": type(exc).__name__}
    finally:
        await close_browser()

print(json.dumps({
    "uid": os.getuid(), "gid": os.getgid(), "home": os.environ.get("HOME"), "umask": umask(),
    "machine": platform.machine(),
    "playwright": importlib.metadata.version("playwright"),
    "modules": mods, "chromium": asyncio.run(chromium()),
    "write": {p: writable(p) for p in ("/tmp", "/app", "/app/dps", "/opt/venv/lib",
                                       "/ms-playwright", "@CHROMIUM@", "/app/var/artefacts",
                                       "/app/var/exports")},
    "home_writable": writable(os.environ.get("HOME") or "/"),
    "group_writable_code": [str(p) for p in pathlib.Path("/app").rglob("*")
                            if not str(p).startswith("/app/var") and p.stat().st_mode & 0o020][:5],
    "group_writable_venv": [str(p) for p in pathlib.Path("/opt/venv").rglob("*")
                            if not p.is_symlink() and p.stat().st_mode & 0o020
                            and p.stat().st_gid == 0][:5],
}))
""".replace("@CHROMIUM@", CHROMIUM)


def _run(image: str, user: str | None, volumes: list[str], *args: str) -> str:
    """``docker run`` as OpenShift's restricted SCC would, through the image's entrypoint, with
    compose's shared memory for Chromium; returns stdout (fails the test on a non-zero exit)."""
    cmd = ["docker", "run", "--rm"]
    if PLATFORM:
        cmd += ["--platform", PLATFORM]
    if user:
        cmd += ["--user", user]
    cmd += ["--read-only", "--tmpfs", "/tmp:size=512m,mode=1777", "--shm-size", "1g"]
    cmd += ["--cap-drop", "ALL", "--security-opt", "no-new-privileges:true"]
    for volume in volumes:
        cmd += ["-v", volume]
    out = subprocess.run(
        [*cmd, image, *args], capture_output=True, text=True, timeout=600, check=False
    )
    assert out.returncode == 0, out.stderr[-2000:]
    return out.stdout


def test_an_arbitrary_uid_runs_chromium_and_writes_only_tmp_and_the_volumes() -> None:
    out = _run(IMAGE, f"{UID}:0", ["/app/var/artefacts", "/app/var/exports"], "python", "-c", PROBE)
    facts = json.loads(out.strip().splitlines()[-1])
    assert facts["machine"] in MACHINES.values(), facts["machine"]
    if PLATFORM:  # an amd64 run really runs the amd64 build (Chromium under emulation)
        assert facts["machine"] == MACHINES[PLATFORM], (PLATFORM, facts["machine"])
    assert (facts["uid"], facts["gid"]) == (int(UID), 0)
    # The entrypoint's umask (what the process creates stays group-writable) and HOME on the
    # /tmp scratch mount (Chromium, fontconfig and Playwright write under ~).
    assert facts["umask"] == "0002", facts["umask"]
    assert facts["home"] == "/tmp" and facts["home_writable"] is True
    assert facts["playwright"] == "1.55.0"
    assert all(v is True for v in facts["modules"].values()), facts["modules"]
    assert facts["chromium"].get("text") == "chromium ok", facts["chromium"]
    assert facts["chromium"].get("png") is True, facts["chromium"]
    write = facts["write"]
    assert write["/tmp"] and write["/app/var/artefacts"] and write["/app/var/exports"], write
    assert not any(write[p] for p in ("/app", "/app/dps", "/opt/venv/lib")), write
    # The base ships /ms-playwright mode 0777: only the read-only root keeps the browsers intact.
    assert not write["/ms-playwright"] and not write[CHROMIUM], write
    assert facts["group_writable_code"] == []
    assert facts["group_writable_venv"] == []


@pytest.fixture
def volumes() -> Iterator[dict[str, str]]:
    """A fresh artefact and export volume pair, removed afterwards."""
    names = {name: f"dps-uid-probe-{name}-{uuid.uuid4().hex[:8]}" for name in ("a", "e")}
    try:
        for volume in names.values():
            made = subprocess.run(
                ["docker", "volume", "create", volume], capture_output=True, text=True, timeout=60
            )
            assert made.returncode == 0, made.stderr
        yield names
    finally:
        for volume in names.values():
            subprocess.run(
                ["docker", "volume", "rm", "-f", volume], capture_output=True, timeout=60
            )


def _store(image: str, user: str | None, names: dict[str, str], step: str) -> dict[str, Any]:
    mounts = [f"{names['a']}:/app/var/artefacts", f"{names['e']}:/app/var/exports"]
    out = _run(image, user, mounts, "python", "-c", STORE_PROBE, step)
    facts: dict[str, Any] = json.loads(out.strip().splitlines()[-1])
    return facts


def _assert_round_trip(
    first: dict[str, Any], second: dict[str, Any], third: dict[str, Any]
) -> None:
    for volume in ("artefacts", "exports"):
        assert first[volume]["file"] == {"mode": "0o660", "gid": 0}, first
        assert first[volume]["dir"] == {"mode": "0o2775", "gid": 0}, first
        facts = second[volume]
        assert facts["read"] is True and facts["streamed"] is True, second
        assert facts["file"] == {"mode": "0o660", "gid": 0}, second
        assert facts["new_dir"] == {"mode": "0o2775", "gid": 0}, second
        assert facts["deleted"] is True, second
    assert sorted(third) == ["artefacts", "exports"]
    assert all(third[v] == {"read": True, "read_new": True} for v in third), third


def test_a_new_uid_reads_and_replaces_what_the_browser_worker_stored(
    volumes: dict[str, str],
) -> None:
    """Through the scanner's own ``LocalObjectStore``: the image user (compose's uid 10001) stores
    an object on an artefact and an export volume, a new UID of group 0 reads, streams, replaces
    and writes next to it, and uid 10001 reads back what the new UID wrote."""
    first = _store(IMAGE, None, volumes, "first")
    second = _store(IMAGE, f"{UID}:0", volumes, "second")
    third = _store(IMAGE, None, volumes, "third")
    _assert_round_trip(first, second, third)


@pytest.mark.skipif(not APP_IMAGE, reason="set DPS_TEST_IMAGE too: the app image reads it")
def test_the_app_image_serves_what_the_browser_worker_stored(volumes: dict[str, str]) -> None:
    """The browser-worker (uid 10001) stores a screenshot; the app's image, as another UID of
    group 0 (a redeploy, an OpenShift namespace UID), reads and replaces it; the browser image
    reads back what the app wrote."""
    first = _store(IMAGE, None, volumes, "first")
    second = _store(APP_IMAGE, f"{UID}:0", volumes, "second")
    third = _store(IMAGE, None, volumes, "third")
    _assert_round_trip(first, second, third)
