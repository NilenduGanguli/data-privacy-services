"""The images run under an arbitrary UID with a read-only root (M5 Task 24, contract §11.1).

Offline: reads ``Dockerfile`` (and ``Dockerfile.browser`` once M4 has merged). OpenShift's restricted
SCC starts a container as a random UID whose only group is 0, with a read-only root filesystem; the
image must therefore (a) be readable by group 0, (b) be writable by group 0 exactly where the image
user writes (``/app/var``: artefact and export volumes), never in the code, and (c) keep ``HOME`` on
the ``/tmp`` scratch mount. A built image is checked by ``tests/test_image_runtime.py``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "Dockerfile").read_text("utf-8")
BROWSER = ROOT / "Dockerfile.browser"
#: The extras every app image installs (contract §11.1) and the opt-in suffix (``EXTRAS``).
BASE_EXTRAS = "fpe,docs,db,connectors,tabular"
OPT_IN = "${EXTRAS:+,$EXTRAS}"


def _runs(text: str) -> str:
    """The Dockerfile with line continuations joined (one logical instruction per line)."""
    return re.sub(r"\\\n\s*", " ", text)


def _instructions(text: str, name: str) -> list[str]:
    return [
        line.strip()
        for line in _runs(text).splitlines()
        if line.strip().upper().startswith(name.upper() + " ")
    ]


def _stages(text: str) -> list[str]:
    return _instructions(text, "FROM")


def test_the_console_stage_builds_on_the_build_host_and_the_runtime_on_the_target() -> None:
    stages = _stages(DOCKERFILE)
    assert len(stages) == 2, stages
    # The dist is plain JS/CSS: building it natively keeps a --platform linux/amd64 build on an
    # arm64 Mac out of emulation for npm; the runtime stage follows the requested platform.
    assert stages[0] == "FROM --platform=$BUILDPLATFORM node:22-slim AS frontend"
    assert stages[1] == "FROM python:3.12-slim"


def test_the_extras_build_arg_adds_opt_in_extras_to_both_install_lines() -> None:
    assert 'ARG EXTRAS=""' in DOCKERFILE
    installs = re.findall(r'uv pip install --system --no-cache -e "\.\[([^\]]+)\]"', DOCKERFILE)
    assert installs == [f"fpe,ner,docs,db,connectors,tabular{OPT_IN}", f"{BASE_EXTRAS}{OPT_IN}"]
    # A mistyped EXTRAS fails the build early instead of reaching pip as a shell fragment.
    run = _runs(DOCKERFILE)
    assert re.search(r'case "\$EXTRAS" in \*\[!a-z0-9,-\]\*\) .*exit 2', run), "EXTRAS guard"


def _install_script() -> str:
    """The Dockerfile's install RUN as a shell script that prints what it would install."""
    run = next(line for line in _instructions(DOCKERFILE, "RUN") if "uv pip install" in line)
    script = run.removeprefix("RUN ")
    script = script.replace("uv pip install --system --no-cache -e", "printf '%s\\n'")
    return script.replace("python -m spacy download en_core_web_sm", "true")


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")
@pytest.mark.parametrize(
    ("extras", "with_ner", "expected"),
    [
        ("", "0", f".[{BASE_EXTRAS}]"),
        ("synth-ml", "0", f".[{BASE_EXTRAS},synth-ml]"),
        ("vertex,synth-ml", "0", f".[{BASE_EXTRAS},vertex,synth-ml]"),
        ("", "1", ".[fpe,ner,docs,db,connectors,tabular]"),
        ("vertex", "1", ".[fpe,ner,docs,db,connectors,tabular,vertex]"),
    ],
)
def test_the_install_line_expands_the_opt_in_extras(
    extras: str, with_ner: str, expected: str
) -> None:
    """The real RUN line under ``sh``: ``,<extras>`` is added only for a non-empty argument."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "EXTRAS": extras, "WITH_NER": with_ner}
    out = subprocess.run(
        ["sh", "-c", _install_script()], env=env, capture_output=True, text=True, timeout=10
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == expected


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")
@pytest.mark.parametrize("extras", ["synth-ml]; rm -rf /", "Synth_ML", "a b", "x$(id)"])
def test_a_malformed_extras_argument_stops_the_build(extras: str) -> None:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "EXTRAS": extras, "WITH_NER": "0"}
    out = subprocess.run(
        ["sh", "-c", _install_script()], env=env, capture_output=True, text=True, timeout=10
    )
    assert out.returncode == 2 and out.stdout == ""
    assert extras not in out.stderr  # the message names the rule, not the argument


def test_the_runtime_is_arbitrary_uid_safe_and_keeps_the_code_read_only() -> None:
    run = _runs(DOCKERFILE)
    assert "ENV HOME=/tmp" in DOCKERFILE
    assert "chgrp -R 0 /app" in run
    # Group 0 reads everything and writes nothing in the code ...
    assert "chmod -R g+rX,g-w /app" in run
    # ... and writes exactly where the image user does (the artefact and export volumes).
    assert "chmod -R g=u /app/var" in run
    assert run.index("chmod -R g+rX,g-w /app") < run.index("chmod -R g=u /app/var")
    # The plan's `chmod -R g=u /app` would make the code writable to every OpenShift UID.
    assert not re.search(r"chmod -R g=u /app(\s|$)", run)
    assert _instructions(DOCKERFILE, "USER") == ["USER 10001"]
    assert "PYTHONDONTWRITEBYTECODE=1" in DOCKERFILE  # nothing is written next to the code


def test_files_written_at_runtime_stay_writable_for_any_uid_of_group_0() -> None:
    """A volume outlives a UID: compose runs uid 10001, OpenShift a namespace UID, and a redeploy
    may change it. Everything the processes create under /app/var must therefore belong to group 0
    and be group-writable, or the next UID fails with ``storage_error`` (measured: artefacts written
    by uid 10001 as 10001:10001 0755 refused a later UID 1000770000:0)."""
    run = _runs(DOCKERFILE)
    # the image user's primary group is 0, so compose's uid 10001 creates group-0 files ...
    assert re.search(
        r"useradd [^&]*--uid 10001 [^&]*--gid 0\b|useradd [^&]*--gid 0 [^&]*--uid 10001", run
    )
    # ... directories under /app/var hand group 0 down to what is created in them (setgid) ...
    assert "find /app/var -type d -exec chmod g+s {} +" in run
    # ... and the entrypoint creates files group-writable (umask 0002).
    entrypoint = (ROOT / "docker" / "entrypoint.sh").read_text("utf-8")
    lines = [
        line.strip()
        for line in entrypoint.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert "umask 0002" in lines
    assert lines.index("umask 0002") < next(
        i for i, line in enumerate(lines) if line.startswith("exec ")
    )


def test_the_arbitrary_uid_lines_come_after_every_copy() -> None:
    """Files copied after the permission step would keep their build-context modes."""
    lines = _runs(DOCKERFILE).splitlines()
    perm = max(i for i, line in enumerate(lines) if "chmod -R g=u /app/var" in line)
    copies = [i for i, line in enumerate(lines) if line.strip().startswith("COPY ")]
    runtime = max(i for i, line in enumerate(lines) if line.strip().startswith("FROM "))
    assert all(i < perm for i in copies if i > runtime)


@pytest.mark.skipif(not BROWSER.exists(), reason="Dockerfile.browser arrives with the M4 merge")
def test_the_browser_image_is_arbitrary_uid_safe() -> None:
    """When M4's browser-worker image is on this branch it follows the same rules: it writes the
    same artefact volume (an arbitrary UID could not, measured on branch ``m4``'s image). In
    Dockerfile.browser (HANDOFF §8): ``useradd … --uid 10001 --gid 0``, then after its last COPY

        COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
        RUN chmod 0755 /usr/local/bin/entrypoint.sh \\
            && chgrp -R 0 /app && chmod -R g+rX,g-w /app && chmod -R g=u /app/var \\
            && find /app/var -type d -exec chmod g+s {} +
        ENV HOME=/tmp
        ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

    /ms-playwright is left as the base image ships it (mode 0777 throughout, so every UID can run
    the browsers): a recursive chgrp/chmod there copies every browser file into a new layer
    (measured: +1.46 GB). The browser-worker therefore always runs with a read-only root, which is
    what keeps the browsers unwritable (compose: ``extends: worker``; OpenShift: Task 25).
    """
    text = BROWSER.read_text("utf-8")
    run = _runs(text)
    assert "ENV HOME=/tmp" in text, "Dockerfile.browser: ENV HOME=/tmp (HANDOFF §8)"
    assert re.search(r"useradd [^&]*--gid 0\b", run), "the image user's primary group is 0"
    assert "chgrp -R 0 /app" in run and "chmod -R g+rX,g-w /app" in run
    assert "chmod -R g=u /app/var" in run
    assert "find /app/var -type d -exec chmod g+s {} +" in run
    assert not re.search(r"chmod -R g=u /app(\s|$)", run)
    assert not re.search(r"(chgrp|chmod|chown) -R [^&]*/ms-playwright", run), "+1.46 GB layer"
    assert _instructions(text, "ENTRYPOINT") == ['ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]']
    assert _instructions(text, "USER") == ["USER 10001"]
