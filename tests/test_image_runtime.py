"""A built app image runs under an arbitrary UID in group 0 with a read-only root (M5 Task 24).

Opt-in: ``DPS_TEST_IMAGE=data-privacy-services:<tag> pytest -q tests/test_image_runtime.py``
(``DPS_TEST_IMAGE_PLATFORM=linux/amd64`` checks an amd64 build under emulation). Each check starts
a throw-away container the way OpenShift's restricted SCC does — a random UID whose only group is 0,
``--read-only`` with a ``/tmp`` tmpfs, no capabilities — and the artefact and export directories as
fresh volumes (they inherit the image's ownership and mode, as compose's named volumes and
OpenShift's emptyDirs do). Nothing here needs the stack.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid

import pytest

IMAGE = os.environ.get("DPS_TEST_IMAGE", "")
PLATFORM = os.environ.get("DPS_TEST_IMAGE_PLATFORM", "")
#: An OpenShift-style UID: high, absent from /etc/passwd, group 0.
UID = "1000770000"

pytestmark = pytest.mark.skipif(
    not IMAGE or shutil.which("docker") is None,
    reason="set DPS_TEST_IMAGE=data-privacy-services:<tag> (and have docker) to check a built image",
)


def _docker_run(*args: str, entrypoint: str | None = None, timeout: int = 180) -> str:
    cmd = ["docker", "run", "--rm"]
    if PLATFORM:
        cmd += ["--platform", PLATFORM]
    cmd += [
        "--user",
        f"{UID}:0",
        "--read-only",
        "--tmpfs",
        "/tmp:size=64m,mode=1777",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "-v",
        "/app/var/artefacts",
        "-v",
        "/app/var/exports",
    ]
    if entrypoint:
        cmd += ["--entrypoint", entrypoint]
    out = subprocess.run(
        [*cmd, IMAGE, *args], capture_output=True, text=True, timeout=timeout, check=False
    )
    assert out.returncode == 0, out.stderr[-2000:]
    return out.stdout


PROBE = r"""
import importlib, json, os, pathlib, shutil, subprocess

def writable(path):
    p = pathlib.Path(path) / ".dps-write-probe"
    try:
        p.write_text("x")
        p.unlink()
        return True
    except OSError:
        return False

mods = {}
for name in ("dps.app", "numpy", "scipy", "fastfpe", "asyncpg", "sqlglot", "pyarrow", "boto3",
             "oracledb", "pymongo", "pypdf", "pdfplumber", "pypdfium2", "reportlab", "docx", "PIL"):
    try:
        importlib.import_module(name)
        mods[name] = True
    except Exception as exc:  # noqa: BLE001
        mods[name] = type(exc).__name__
tess = subprocess.run(["tesseract", "--version"], capture_output=True, text=True)
print(json.dumps({
    "uid": os.getuid(), "gid": os.getgid(), "home": os.environ.get("HOME"),
    "modules": mods, "tesseract": tess.returncode == 0,
    "write": {p: writable(p) for p in ("/tmp", "/app", "/app/dps", "/app/frontend/dist",
                                       "/app/tools", "/app/var/artefacts", "/app/var/exports",
                                       "/usr/local/lib/python3.12/site-packages")},
    "home_writable": writable(os.environ.get("HOME") or "/"),
    "console": pathlib.Path("/app/frontend/dist/index.html").is_file(),
    "group_writable_code": [str(p) for p in pathlib.Path("/app").rglob("*")
                            if not str(p).startswith("/app/var") and p.stat().st_mode & 0o020][:5],
    "group_writable_system": [str(p) for root in ("/usr/local", "/etc")
                              for p in pathlib.Path(root).rglob("*")
                              if not p.is_symlink() and p.stat().st_mode & 0o020
                              and p.stat().st_gid == 0][:5],
}))
"""


def test_an_arbitrary_uid_reads_the_code_and_writes_only_tmp_and_the_volumes() -> None:
    facts = json.loads(_docker_run("-c", PROBE, entrypoint="python").strip().splitlines()[-1])
    assert (facts["uid"], facts["gid"]) == (int(UID), 0)
    assert facts["home"] == "/tmp" and facts["home_writable"] is True
    assert all(v is True for v in facts["modules"].values()), facts["modules"]
    assert facts["tesseract"] is True and facts["console"] is True
    write = facts["write"]
    assert write["/tmp"] and write["/app/var/artefacts"] and write["/app/var/exports"], write
    assert not any(write[p] for p in ("/app", "/app/dps", "/app/frontend/dist", "/app/tools"))
    assert not write["/usr/local/lib/python3.12/site-packages"]
    assert facts["group_writable_code"] == []
    # The image user's group is 0 (compose and OpenShift alike): nothing it may not change is
    # group-0 writable — the interpreter, the installed packages, the system configuration.
    assert facts["group_writable_system"] == []


def test_the_app_serves_health_under_an_arbitrary_uid() -> None:
    name = f"dps-uid-probe-{uuid.uuid4().hex[:8]}"
    cmd = ["docker", "run", "-d", "--name", name]
    if PLATFORM:
        cmd += ["--platform", PLATFORM]
    cmd += [
        "--user",
        f"{UID}:0",
        "--read-only",
        "--tmpfs",
        "/tmp:size=64m,mode=1777",
        "--cap-drop",
        "ALL",
        "-e",
        "DPS_ENV=local",
        "-e",
        "DPS_AUTH_ENABLED=false",
        "-e",
        "DPS_LLM_PROVIDER=stub",
        IMAGE,
    ]
    started = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    assert started.returncode == 0, started.stderr[-2000:]
    try:
        probe = (
            "import urllib.request,sys; "
            "sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8400/health', timeout=3)"
            ".status == 200 else 1)"
        )
        deadline = time.monotonic() + (240 if PLATFORM else 90)
        ok = False
        while time.monotonic() < deadline and not ok:
            ok = (
                subprocess.run(
                    ["docker", "exec", name, "python", "-c", probe],
                    capture_output=True,
                    timeout=30,
                    check=False,
                ).returncode
                == 0
            )
            if not ok:
                time.sleep(2)
        logs = subprocess.run(
            ["docker", "logs", name], capture_output=True, text=True, timeout=30, check=False
        )
        assert ok, (logs.stdout + logs.stderr)[-3000:]
        assert "PermissionError" not in logs.stdout + logs.stderr
        assert "Read-only file system" not in logs.stdout + logs.stderr
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60, check=False)


def test_a_new_uid_keeps_writing_where_the_previous_one_wrote() -> None:
    """Compose's uid 10001 writes artefacts into a named volume; a later container under an
    arbitrary UID (a redeploy, an OpenShift namespace UID) writes into the same directories."""
    volume = f"dps-uid-probe-{uuid.uuid4().hex[:8]}"
    created = subprocess.run(
        ["docker", "volume", "create", volume], capture_output=True, text=True, timeout=60
    )
    assert created.returncode == 0, created.stderr
    script = (
        "import os, pathlib, sys; d = pathlib.Path('/app/var/artefacts/t/default') / sys.argv[1]; "
        "d.mkdir(parents=True, exist_ok=True); (d / 'a.bin').write_bytes(b'x'); "
        "pathlib.Path('/app/var/artefacts/t/default/a.json').write_text('{}'); "
        "print(oct(os.stat(d).st_mode & 0o7777), os.stat(d).st_gid)"
    )
    try:

        def run(user: str | None, sub: str) -> subprocess.CompletedProcess[str]:
            cmd = ["docker", "run", "--rm"]
            if PLATFORM:
                cmd += ["--platform", PLATFORM]
            if user:
                cmd += ["--user", user]
            cmd += ["--read-only", "--tmpfs", "/tmp", "-v", f"{volume}:/app/var/artefacts"]
            cmd += [
                "--entrypoint",
                "/usr/local/bin/entrypoint.sh",
                IMAGE,
                "python",
                "-c",
                script,
                sub,
            ]
            return subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)

        first = run(None, "job-a")  # the image's own user (uid 10001, compose)
        assert first.returncode == 0, first.stderr[-2000:]
        mode, gid = first.stdout.split()
        assert gid == "0" and int(mode, 8) & 0o2070 == 0o2070, (mode, gid)
        second = run(f"{UID}:0", "job-b")  # a new UID in group 0, same directories
        assert second.returncode == 0, second.stderr[-2000:]
        rewrite = run(f"{UID}:0", "job-a")  # and into a directory the first UID created
        assert rewrite.returncode == 0, rewrite.stderr[-2000:]
    finally:
        subprocess.run(["docker", "volume", "rm", "-f", volume], capture_output=True, timeout=60)
