"""S4: the selection validators are the only barrier before SQL and BSON — an adversarial corpus
over both SQL dialects and the MongoDB filter allow-list (offline; M2/M3's functions)."""

from __future__ import annotations

from typing import Any

import pytest

from dps.jobs_models import Selection
from dps.layers.connectors.base import ColumnInfo, Engine, ObjectRef, TableShape
from dps.layers.connectors.selection import (
    SelectionError,
    compile_where,
    parameterize_literals,
    quote_ident,
    validate_mongo_filter,
    validate_selection,
)

ENGINES: tuple[Engine, ...] = ("postgres", "oracle")


def _shape(engine: Engine) -> TableShape:
    up = (lambda n: n.upper()) if engine == "oracle" else (lambda n: n)
    cols = [
        ColumnInfo(path="$.id", name=up("id"), data_type="int", kind="int", is_key=True),
        ColumnInfo(path="$.email", name=up("email"), data_type="text", kind="text"),
        ColumnInfo(path="$.name", name=up("name"), data_type="text", kind="text"),
    ]
    owner = "APP" if engine == "oracle" else "public"
    return TableShape(
        object=ObjectRef(owner=owner, name=up("contacts")),
        columns=cols,
        key=[up("id")],
        key_source="primary_key",
    )


WHERE = [
    "1=1; DROP TABLE customers",
    "id = 1 UNION SELECT password FROM users",
    "pg_sleep(10) IS NULL",
    "dbms_pipe.receive_message('x', 10) = 1",
    "id IN (SELECT id FROM dual) FOR UPDATE",
    "utl_http.request('http://x') IS NOT NULL",
    "1=1 /* */ ; COMMIT",
    "id = pg_read_file('/etc/passwd')::int",
    "id IN (SELECT dblink('x','select 1'))",
    "id = 1 OR 1=(SELECT 1 FROM pg_sleep(5))",
]
QUERY = [
    "DELETE FROM contacts",
    "SELECT * INTO x FROM contacts",
    "SELECT 1; SELECT 2",
    "SELECT * FROM contacts AS c(id, email2, name)",  # column re-mapping (M3-B6)
    "SELECT id, email, name FROM contacts UNPIVOT (v FOR k IN (email, name))",
    "SELECT id, email, name FROM other_table",
]


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("where", WHERE)
def test_where_injection_is_refused(engine: Engine, where: str) -> None:
    with pytest.raises(SelectionError):
        validate_selection(Selection(where=where), engine, _shape(engine))


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("where", ["name = 'x' --", "name = 'x' /* ; DROP TABLE t */"])
def test_a_comment_never_reaches_the_statement(engine: Engine, where: str) -> None:
    """M2 strips comments instead of refusing them (the plan's corpus expected a refusal): the
    statement DPS runs is rendered from the parse tree, so a comment cannot hide DPS's own SQL."""
    bound = parameterize_literals(Selection(where=where), engine)
    validate_selection(bound, engine, _shape(engine))
    fragment, _ = compile_where(bound, engine)
    assert "--" not in fragment and "/*" not in fragment and "DROP" not in fragment.upper()


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("query", QUERY)
def test_query_injection_is_refused(engine: Engine, query: str) -> None:
    with pytest.raises(SelectionError):
        validate_selection(Selection(query=query), engine, _shape(engine))


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("name", ['name"; DROP', "a b", "1abc", "x\x00y"])
def test_bad_identifiers_are_refused(engine: Engine, name: str) -> None:
    with pytest.raises(SelectionError):
        quote_ident(name, engine)


@pytest.mark.parametrize(
    "flt",
    [
        {"$where": "sleep(1000)"},
        {"a": {"$function": {}}},
        {"$expr": {"$gt": ["$a", 1]}},
        {"a": {"$accumulator": {}}},
        {"$or": [{"a": 1}, {"$where": "1"}]},
        {"a": {"$elemMatch": {"$where": "1"}}},
        {"a": {"$in": [{"$where": "1"}]}},
    ],
)
def test_mongo_operators_outside_the_allow_list_are_refused(flt: dict[str, Any]) -> None:
    with pytest.raises(SelectionError) as caught:
        validate_mongo_filter(flt)
    assert caught.value.code == "selection_operator_denied"


def test_refusals_never_quote_the_selection() -> None:
    with pytest.raises(SelectionError) as caught:
        validate_selection(
            Selection(where="name = 'S3NT1NEL' ; DROP TABLE t"), "postgres", _shape("postgres")
        )
    assert "S3NT1NEL" not in str(caught.value)
