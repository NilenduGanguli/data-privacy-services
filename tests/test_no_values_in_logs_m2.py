"""No customer value, key, seed, password or selection literal reaches a log line (contract §0.1).

Static part (offline): every log call in an M2 module is ``logger.<level>("<snake_case_event>",
<allow-listed field>=…)``, with no positional format arguments, no ``print`` and no stdlib logger
(which would bypass the allow-list). Dynamic part (``db``, ``pg_source``): a whole run job — discovery,
prepare, partitions, finalize — logs nothing of the rows it read, its key, the KEK, the source
password or the selection's literal, and every JSON line holds allow-listed fields only.
"""

from __future__ import annotations

import ast
import io
import json
import logging
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from dps.observability import _EVENT_NAME, _SAFE_FIELDS

if TYPE_CHECKING:
    from dps.connections import Connection
    from dps.worker import HandlerContext
    from tests.fixtures.sources import SourcePg

ROOT = Path(__file__).resolve().parents[1]
DPS = ROOT / "dps"
M2_MODULES = sorted(
    [
        *DPS.glob("jobs*.py"),
        DPS / "worker.py",
        DPS / "retention.py",
        DPS / "db.py",
        DPS / "connections.py",
        DPS / "snapshots.py",
        DPS / "groups.py",
        DPS / "storage.py",
        DPS / "secrets.py",
        DPS / "audit.py",
        DPS / "engine" / "batch.py",
        *(DPS / "layers" / "connectors").glob("*.py"),
        DPS / "layers" / "api" / "errors.py",
        DPS / "layers" / "api" / "deps.py",
        *[
            DPS / "layers" / "api" / "routers" / f"{n}.py"
            for n in ("connections", "jobs", "snapshots", "groups", "evidence", "artefacts")
        ],
    ]
)
LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}
#: The one splat allowed in a log call: the audit line, whose fields are AuditEvent's (checked below).
SPLAT_ALLOWED = {("audit.py", "audit")}


def _log_calls(tree: ast.AST) -> Iterator[ast.Call]:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in LOG_METHODS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "logger"
        ):
            yield node


def _calls_named(tree: ast.AST, name: str) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
    ]


def _stdlib_logging(tree: ast.AST) -> list[int]:
    """``logging.getLogger(...)`` / ``logging.info(...)``: the stdlib bypasses the allow-list."""
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "logging"
        and node.func.attr in LOG_METHODS | {"getLogger", "log"}
    ]


def test_the_scan_covers_every_m2_module() -> None:
    assert all(p.is_file() for p in M2_MODULES)
    names = {p.name for p in M2_MODULES}
    assert {
        "jobs.py",
        "jobs_run.py",
        "worker.py",
        "postgres.py",
        "files.py",
        "evidence.py",
    } <= names
    assert len(M2_MODULES) >= 30


@pytest.mark.parametrize("path", M2_MODULES, ids=lambda p: str(p.relative_to(DPS)))
def test_log_calls_use_snake_case_events_and_safe_fields(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for call in _log_calls(tree):
        where = f"{path.name}:{call.lineno}"
        assert (
            call.args
            and isinstance(call.args[0], ast.Constant)
            and isinstance(call.args[0].value, str)
        ), f"{where} log event must be a string literal"
        event = call.args[0].value
        assert _EVENT_NAME.fullmatch(event), f"{where} log event {event!r} is not a snake_case name"
        assert len(call.args) == 1, f"{where} no positional format args"
        if any(kw.arg is None for kw in call.keywords):
            assert (path.name, event) in SPLAT_ALLOWED, f"{where} a ** splat in a log call"
        unsafe = {kw.arg for kw in call.keywords if kw.arg is not None} - _SAFE_FIELDS
        assert not unsafe, f"{where} unsafe log fields {unsafe}"
    assert not _calls_named(tree, "print"), f"{path.name} prints at {_calls_named(tree, 'print')}"
    assert not _stdlib_logging(tree), f"{path.name} uses stdlib logging at {_stdlib_logging(tree)}"


def test_the_scanner_finds_what_it_must_refuse() -> None:
    bad = ast.parse(
        "logger.info('row %s', value)\n"
        "logger.info(f'row {value}')\n"
        "logger.info('row_read', full_name=value)\n"
        "logger.info('Row Read')\n"
        "logger.info('row_read', **row)\n"
        "print(value)\n"
        "logging.getLogger(__name__).info('x')\n"
    )
    calls = list(_log_calls(bad))
    assert len(calls) == 5
    assert len(calls[0].args) == 2  # positional format argument
    assert not isinstance(calls[1].args[0], ast.Constant)  # f-string event
    assert {kw.arg for kw in calls[2].keywords} - _SAFE_FIELDS == {"full_name"}
    assert not _EVENT_NAME.fullmatch(calls[3].args[0].value)  # type: ignore[attr-defined]
    assert any(kw.arg is None for kw in calls[4].keywords)  # splat
    assert _calls_named(bad, "print") == [6]
    assert _stdlib_logging(bad) == [7]
    fine = ast.parse("seed_fingerprint(x)\nkey_fingerprint(y)\n")
    assert _calls_named(fine, "print") == []  # the plan's substring check matched these


def test_the_audit_line_carries_allow_listed_fields_only() -> None:
    from dps.audit import AuditEvent

    assert set(AuditEvent.model_fields) <= _SAFE_FIELDS


@pytest.fixture
def json_logs() -> Iterator[io.StringIO]:
    """structlog as in production (JSON, DEBUG) plus a root handler; the old config is restored."""
    import structlog

    from dps.observability import configure_logging

    old = structlog.get_config()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    old_level = root.level
    configure_logging("DEBUG", json_output=True)
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield stream
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
        structlog.configure(**old)


#: A distinctive selection literal: it must reach the database as a bound parameter, never a log.
LITERAL = "logscan-literal@example.invalid"


@pytest.mark.db
@pytest.mark.integration
@pytest.mark.pg_source
async def test_a_full_job_logs_no_values(
    handler_ctx: HandlerContext,
    source_pg: SourcePg,
    register_source: Callable[..., Awaitable[Connection]],
    json_logs: io.StringIO,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from dps.auth import LOCAL_PRINCIPAL
    from dps.jobs import JobState
    from dps.layers.connectors.submit import submit_run
    from tests.connectors.helpers import discover_and_accept, drive
    from tests.connectors.test_run_modes import run_spec

    obj = await source_pg.customers_table(rows=500, name="logscan")
    src = await register_source("t1", name="src-logscan")
    uat = await register_source(
        "t1", ref="dps/src/test-pg-rw", roles={"target"}, name="uat-logscan"
    )
    snap = await discover_and_accept(handler_ctx, "t1", src.id, obj)
    where = f"full_name LIKE 'A%' AND email <> '{LITERAL}'"
    sub = await submit_run(
        run_spec(
            src.id,
            obj,
            snap.id,
            {
                "write_mode": "table",
                "connection_id": uat.id,
                "object": {"owner": source_pg.schema, "name": "logscan_out"},
            },
            source={"selection": {"where": where}},
        ),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=handler_ctx.settings,
        kek=handler_ctx.kek,
    )
    done = await drive(handler_ctx, "t1", sub.job.id)
    assert done.state == JobState.succeeded, done.error_code
    assert sub.key_material is not None

    cap = capsys.readouterr()
    captured = json_logs.getvalue() + cap.out + cap.err
    rows = await source_pg.admin.fetch(
        f'SELECT full_name, email, national_id, mobile, tax_id FROM "{source_pg.schema}".logscan '
        "WHERE full_name LIKE 'A%'"
    )
    written = await source_pg.admin.fetchval(
        f'SELECT count(*) FROM "{source_pg.schema}".logscan_out'
    )
    assert rows and written == len(rows), "the job must have read and written the selected rows"
    values = [str(v) for r in rows for v in r.values() if v is not None and len(str(v)) >= 6]
    secrets_ = [
        *values,
        sub.key_material["dek"],
        handler_ctx.settings.job_kek,
        LITERAL,
        "dps_reader",  # the local source password equals its login
        "dps_writer",
    ]
    leaked = [i for i, s in enumerate(secrets_) if s and s in captured]
    assert not leaked, f"{len(leaked)} values reached the logs"  # positions only, never values
    lines = [line for line in captured.splitlines() if line.startswith("{")]
    assert lines, "the job logged nothing to scan"
    events = set()
    for line in lines:
        record = json.loads(line)
        assert set(record) <= _SAFE_FIELDS, set(record) - _SAFE_FIELDS
        events.add(record.get("event"))
    assert {"discover_done", "prepare_done", "finalize_done"} <= events
