"""M3 log scan: static AST checks plus a dynamic sentinel run (contract §0.1).

Static part (offline): every log call in an M3 module is ``logger.<level>("<snake_case_event>",
<allow-listed field>=…)``; no M3 module turns a caught exception into text (``str(exc)``,
``repr(exc)``, an f-string of it, ``.message``/``.errmsg``; ``.details`` only in ``mongo.py``, whose
``mongo_code`` reads the integer server code out of it); every ``ConnectorError`` is built from a
literal code (or a ``code`` parameter that callers fill with literals); service modules never print.

Dynamic part (``oracle`` + ``mongo``): sentinel values flow through failing and succeeding Oracle,
MongoDB and consistency-check paths — (a) an Oracle login whose password is the sentinel, (b) a
``CHAR``-keyed Oracle table of sentinel keys paged, sampled and written in place until a stale ledger
mark raises ``ledger_conflict``, (c) a MongoDB duplicate-key write of a sentinel ``customerId`` (the
server's E11000 message quotes the key), (d) a consistency check over targets holding sentinel tokens,
passing and failing. Nothing sentinel-shaped may reach captured logs (structlog's stdout, stderr and
the stdlib root logger) or the ``str``/``repr``/``args`` of a raised error. A self-test proves the
capture sees a planted sentinel.
"""

from __future__ import annotations

import ast
import io
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from dps.observability import _EVENT_NAME, _SAFE_FIELDS

if TYPE_CHECKING:
    from dps.layers.connectors.base import Engine

ROOT = Path(__file__).resolve().parents[1]
M3 = [
    "dps/layers/connectors/oracle.py",
    "dps/layers/connectors/oracle_sql.py",
    "dps/layers/connectors/mongo.py",
    "dps/layers/connectors/mongo_docs.py",
    "dps/layers/connectors/docpaths.py",
    "dps/layers/connectors/consistency.py",
    "tools/seed_oracle.py",
    "tools/seed_mongo.py",
    "tools/seed_model.py",
]
LEVELS = {"debug", "info", "warning", "error", "exception", "critical"}
#: Names an exception is conventionally bound to; ``except … as <name>`` adds the module's own.
EXC_NAMES = {"exc", "err", "error", "e", "ex"}
SENTINEL = "SENTINEL"


def _tree(rel: str) -> ast.Module:
    return ast.parse((ROOT / rel).read_text(encoding="utf-8"))


def _exception_names(tree: ast.AST) -> set[str]:
    names = set(EXC_NAMES)
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
    return names


def _exception_text_uses(tree: ast.AST, *, details_ok: bool) -> list[int]:
    """Lines that turn a caught exception into text, or read its message/details."""
    names = _exception_names(tree)
    bad: list[int] = []

    def is_exc(node: ast.AST) -> bool:
        return isinstance(node, ast.Name) and node.id in names

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"str", "repr", "format"}
            and node.args
            and is_exc(node.args[0])
        ):
            bad.append(node.lineno)
        elif isinstance(node, ast.FormattedValue) and is_exc(node.value):
            bad.append(node.lineno)
        elif isinstance(node, ast.Attribute) and is_exc(node.value):
            if node.attr in {"message", "errmsg", "_message"} or (
                node.attr == "details" and not details_ok
            ):
                bad.append(node.lineno)
    return sorted(bad)


def test_the_scan_covers_every_m3_module() -> None:
    assert all((ROOT / rel).is_file() for rel in M3), [r for r in M3 if not (ROOT / r).is_file()]


@pytest.mark.parametrize("rel", M3)
def test_log_calls_are_allow_listed(rel: str) -> None:
    for node in ast.walk(_tree(rel)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in LEVELS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "logger"
        ):
            where = f"{rel}:{node.lineno}"
            assert (
                node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ), f"{where} event not literal"
            assert _EVENT_NAME.fullmatch(node.args[0].value), where
            assert len(node.args) == 1, f"{where} positional format args"
            assert all(k.arg is not None for k in node.keywords), f"{where} ** splat"
            bad = [k.arg for k in node.keywords if k.arg not in _SAFE_FIELDS]
            assert not bad, f"{where} fields {bad}"


@pytest.mark.parametrize("rel", M3)
def test_no_exception_text_and_literal_error_codes(rel: str) -> None:
    tree = _tree(rel)
    bad = _exception_text_uses(tree, details_ok=rel.endswith("/mongo.py"))
    assert not bad, f"{rel} turns an exception into text at lines {bad}"
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id == "ConnectorError":
            assert node.args, f"{rel}:{node.lineno} ConnectorError without a code"
            code = node.args[0]
            assert (isinstance(code, ast.Constant) and isinstance(code.value, str)) or (
                isinstance(code, ast.Name) and code.id == "code"
            ), f"{rel}:{node.lineno} ConnectorError code is not a literal"
        if node.func.id == "print":
            assert rel.startswith("tools/"), f"{rel}:{node.lineno} print in a service module"


def test_the_static_scan_finds_what_it_must_refuse() -> None:
    bad = ast.parse(
        "try:\n    pass\nexcept ValueError as boom:\n"
        "    x = str(boom)\n"
        "    y = f'failed {boom}'\n"
        "    z = boom.message\n"
        "    w = repr(exc)\n"
        "    v = exc.details\n"
    )
    assert _exception_text_uses(bad, details_ok=False) == [4, 5, 6, 7, 8]
    assert _exception_text_uses(bad, details_ok=True) == [4, 5, 6, 7]
    fine = ast.parse('"""Never ``str(exc)``."""\nx = exc.code\ny = type(exc).__name__\n')
    assert _exception_text_uses(fine, details_ok=False) == []


# -- dynamic --------------------------------------------------------------------------------------


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


def _captured(json_logs: io.StringIO, capsys: pytest.CaptureFixture[str]) -> str:
    cap = capsys.readouterr()
    return json_logs.getvalue() + cap.out + cap.err


def _error_texts(errors: list[BaseException]) -> list[str]:
    out: list[str] = []
    for err in errors:
        out += [str(err), repr(err), repr(err.args)]
        if err.__cause__ is not None:
            out += [str(err.__cause__), repr(err.__cause__)]
    return out


def test_the_capture_sees_a_planted_sentinel(
    json_logs: io.StringIO, capsys: pytest.CaptureFixture[str]
) -> None:
    """Self-test: a sentinel logged through an allow-listed field reaches the captured text."""
    from dps.observability import get_logger

    get_logger("logscan_m3_selftest").warning("planted_leak", reason=f"{SENTINEL}-PLANTED")
    logging.getLogger("logscan_m3_stdlib").warning("stdlib %s", f"{SENTINEL}-STDLIB")
    captured = _captured(json_logs, capsys)
    assert f"{SENTINEL}-PLANTED" in captured and f"{SENTINEL}-STDLIB" in captured


@pytest.mark.parametrize("engine", ["oracle", "postgres"])
def test_a_refused_selection_never_logs_its_literal(
    engine: Engine, json_logs: io.StringIO, capsys: pytest.CaptureFixture[str]
) -> None:
    """sqlglot parses ``EXPLAIN PLAN FOR …`` as an unsupported command and logs the statement text
    (literal included) through stdlib ``logging``; the ``sqlglot`` cap keeps it out of the logs."""
    from dps.jobs_models import Selection
    from dps.layers.connectors.selection import SelectionError, validate_selection

    sel = Selection(query=f"EXPLAIN PLAN FOR SELECT * FROM t WHERE n = '{SENTINEL}-SEL'")
    with pytest.raises(SelectionError) as refused:
        validate_selection(sel, engine, None)
    assert refused.value.code == "selection_not_select"
    captured = _captured(json_logs, capsys)
    assert SENTINEL not in captured, "a refused selection's literal reached the logs"
    leaked = [i for i, text in enumerate(_error_texts([refused.value])) if SENTINEL in text]
    assert not leaked, f"sentinel text in the raised error at positions {leaked}"


async def _oracle_paths(oracle_conn: Any, schema: Any, errors: list[BaseException]) -> None:
    """(a) a sentinel password fails to log in; (b) sentinel CHAR keys paged, sampled, written in
    place, then a stale ledger mark is refused."""
    from dps.config import Settings
    from dps.jobs_models import ConnectionParams, Selection
    from dps.layers.connectors.base import (
        ConnectorError,
        KeyRange,
        LedgerConflict,
        LedgerMark,
        ObjectRef,
        encode_key,
    )
    from dps.layers.connectors.oracle import OracleConnector
    from tests.connectors.oracle_fixtures import make_target

    def connector(user: str, password: str) -> OracleConnector:
        return OracleConnector.from_secret(
            {"username": user, "password": password, "dsn": schema.dsn},
            ConnectionParams(),
            Settings(_env_file=None),
            action="write",
            client_id="j_logscan",
        )

    bad = connector(schema.ro_user, f"{SENTINEL}-PW-1")
    with pytest.raises(ConnectorError) as login:
        await bad.open()
    assert login.value.code == "connection_failed"
    errors.append(login.value)
    await bad.close()

    o = schema.owner
    keys = [f"{SENTINEL}-K{i}" for i in range(1, 31)]
    with oracle_conn.cursor() as cur:
        cur.execute(
            f'CREATE TABLE "{o}"."LOGSCAN_KYC" (DOC_REF CHAR(16) NOT NULL PRIMARY KEY, '
            "HOLDER VARCHAR2(40) NOT NULL, NOTES CLOB)"
        )
        cur.executemany(
            f'INSERT INTO "{o}"."LOGSCAN_KYC" VALUES (:1, :2, :3)',
            [(k, f"{SENTINEL}-HOLDER-{i}", f"{SENTINEL}-NOTE-{i} \U0001f600") for i, k in
             enumerate(keys)],
        )  # fmt: skip
    oracle_conn.commit()

    c = connector(o, schema.owner_password)
    await c.open()
    try:
        shape = await c.describe(ObjectRef(owner=o, name="LOGSCAN_KYC"))
        assert await c.count(shape, Selection(), {}) == 30
        assert len(await c.sample(shape, Selection(), {}, 10)) == 10
        rows = []
        async for page in c.read_partition(
            shape, Selection(), {}, KeyRange(), after_key=None, batch=7
        ):
            rows += page
        assert len(rows) == 30
        keycols = shape.key_columns()
        writer = await c.writer(
            make_target(shape, write_mode="in_place", write_columns=["HOLDER"]), shape
        )
        await writer.prepare(dry_run=False)

        def mark(batch: int, last: dict[str, Any], n: int) -> LedgerMark:
            return LedgerMark(
                job_id="j_logscan",
                partition_no=0,
                batch_no=batch,
                last_key=encode_key((last["DOC_REF"],), keycols),
                rows=n,
            )

        first = [{**r, "HOLDER": f"masked-{i}"} for i, r in enumerate(rows[:15])]
        assert await writer.write_batch(first, mark=mark(1, rows[14], 15)) == 15
        with pytest.raises(LedgerConflict) as stale:
            await writer.write_batch(first, mark=mark(1, rows[14], 15))
        assert stale.value.code == "ledger_conflict"
        errors.append(stale.value)
        await writer.close()
    finally:
        await c.close()
    with oracle_conn.cursor() as cur:
        cur.execute(f'SELECT COUNT(*) FROM "{o}"."LOGSCAN_KYC" WHERE HOLDER LIKE :p', {"p": "m%"})
        assert cur.fetchone()[0] == 15  # the stale replay wrote nothing


async def _mongo_paths(mongo_client: Any, mongo_db: str, errors: list[BaseException]) -> None:
    """(c) a duplicate-key write of a sentinel ``customerId`` (E11000 quotes the key)."""
    from dps.jobs_models import Selection
    from dps.layers.connectors.base import ConnectorError, KeyRange, LedgerMark, ObjectRef
    from dps.layers.connectors.base import encode_key as enc
    from tests.connectors.mongo_fixtures import connector, profile
    from tests.connectors.oracle_fixtures import make_target
    from tests.fixtures.m3_services import mongo_uri

    mongo_client[mongo_db]["profiles"].insert_many(
        [{**profile(i), "notes": f"{SENTINEL}-NOTE-{i}"} for i in range(3)]
    )
    mongo_client[mongo_db]["profiles_uat"].create_index("customerId", unique=True)
    c = connector(mongo_uri(), mongo_db)
    await c.open()
    try:
        shape = await c.describe(ObjectRef(name="profiles"))
        rows = []
        async for page in c.read_partition(
            shape, Selection(), {}, KeyRange(), after_key=None, batch=2
        ):
            rows += page
        dup = [{**r, "customerId": f"{SENTINEL}-DUP"} for r in rows]
        writer = await c.writer(
            make_target(
                shape, write_mode="table", engine="mongo", object=ObjectRef(name="profiles_uat")
            ),
            shape,
        )
        await writer.prepare(dry_run=False)
        last = enc((rows[-1]["_id"],), shape.key_columns())
        with pytest.raises(ConnectorError) as err:
            await writer.write_batch(
                dup,
                mark=LedgerMark(
                    job_id="j_logscan", partition_no=0, batch_no=1, last_key=last, rows=3
                ),
            )
        assert err.value.code == "pk_collision"
        errors.append(err.value)
        await writer.close()
    finally:
        await c.close()


async def _consistency_paths(errors: list[BaseException]) -> None:
    """(d) a passing and a failing consistency check over targets holding sentinel tokens."""
    from tests.connectors.test_consistency import World

    world = World(200, prefix=f"{SENTINEL}-TOKEN-")
    world.mongo_strings = [f"{SENTINEL}-TOKEN-{t:08d}" for t in world.tokens]  # prefixed ids
    result, _ = await world.run()
    assert result.passed and result.consistent_pairs == 3
    assert SENTINEL not in json.dumps(result.model_dump(mode="json"))

    world = World(200, prefix=f"{SENTINEL}-TOKEN-")
    for k in range(0, 200, 10):  # 10 % of the Mongo member's tokens drifted: the check fails
        t = world.tokens[k]
        world.mongo_tokens[k] = t + 1 if t + 1 not in world.tokens else t - 1
    result, _ = await world.run()
    assert not result.passed
    assert SENTINEL not in json.dumps(result.model_dump(mode="json"))

    fakes = world.connectors()
    fakes["c_mdb"].fail_lookup = True
    with pytest.raises(RuntimeError) as boom:
        await world.run(fakes=fakes)
    errors.append(boom.value)


@pytest.mark.integration
@pytest.mark.oracle
@pytest.mark.mongo
async def test_runtime_sentinels_never_reach_logs(
    oracle_admin: Any,
    oracle_schema: Any,
    oracle_conn: Any,
    mongo_client: Any,
    mongo_db: str,
    json_logs: io.StringIO,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from tests.connectors.oracle_fixtures import drop_all

    errors: list[BaseException] = []
    drop_all(oracle_admin, oracle_schema.owner)
    try:
        await _oracle_paths(oracle_conn, oracle_schema, errors)
        await _mongo_paths(mongo_client, mongo_db, errors)
        await _consistency_paths(errors)
    finally:
        drop_all(oracle_admin, oracle_schema.owner)
    captured = _captured(json_logs, capsys)

    assert len(errors) == 4
    assert SENTINEL not in captured, "a sentinel value reached the logs"
    leaked = [i for i, text in enumerate(_error_texts(errors)) if SENTINEL in text]
    assert not leaked, f"sentinel text in raised errors at positions {leaked}"
    events = set()
    for line in captured.splitlines():
        if line.startswith("{"):
            record = json.loads(line)
            assert set(record) <= _SAFE_FIELDS, set(record) - _SAFE_FIELDS
            events.add(record.get("event"))
    # The driver failures above are mapped without a log line (login refused inside the pool,
    # E11000 -> pk_collision); the check logs its counts-only summary once per run.
    assert "consistency_checked" in events, events
