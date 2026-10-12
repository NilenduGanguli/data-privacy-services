"""SEC-3: which connection params decide where stored credentials go (pure, no database)."""

from __future__ import annotations

import pytest

from dps.connections import (
    ENDPOINT_PARAMS,
    NOT_ENDPOINT_PARAMS,
    Connection,
    ConnectionPatch,
    changed_fields,
    endpoint_changes,
)
from dps.jobs_models import OPTION_KEYS, ConnectionParams
from tests.connectors.factories import make_connection

PG = {"host": "db.example", "port": 5432, "database": "bank"}


def test_one_engine_keyed_list_covers_every_engines_endpoint() -> None:
    assert ENDPOINT_PARAMS["postgres"] >= {
        "host",
        "port",
        "database",
        "dbname",
        "options.sslmode",
        "options.sslrootcert_ref",
    }
    # M3's engines: covered by name before their params exist on main
    assert ENDPOINT_PARAMS["oracle"] >= {"host", "port", "service_name", "sid", "dsn"}
    assert ENDPOINT_PARAMS["mongo"] >= {"uri", "host", "port", "replica_set", "database"}
    assert ENDPOINT_PARAMS["mongo"] >= {"options.replicaSet", "options.directConnection"}
    assert ENDPOINT_PARAMS["s3"] >= {"endpoint_url", "bucket"}
    assert ENDPOINT_PARAMS["pvc"] == frozenset()  # takes no credentials


def test_every_param_is_classified_so_a_new_one_is_a_deliberate_choice() -> None:
    """A param added to ConnectionParams (M3, M5) must be listed as endpoint-defining for its
    engines or as harmless; until then this test fails instead of silently letting a jobs key
    redirect stored credentials through it."""
    fields = {name for name in ConnectionParams.model_fields if name != "options"}
    fields |= {f"options.{key}" for key in OPTION_KEYS}
    endpoint = set().union(*ENDPOINT_PARAMS.values())
    assert fields <= endpoint | NOT_ENDPOINT_PARAMS, sorted(fields - endpoint - NOT_ENDPOINT_PARAMS)
    assert not endpoint & NOT_ENDPOINT_PARAMS


@pytest.mark.parametrize(
    ("new", "expected"),
    [
        ({**PG, "host": "evil.example"}, ["host"]),
        ({**PG, "port": 6543, "database": "x"}, ["database", "port"]),
        ({**PG, "options": {"sslmode": "disable"}}, ["options.sslmode"]),
        ({**PG, "options": {"connect_timeout": "5"}}, []),
        (PG, []),
    ],
)
def test_endpoint_changes_names_fields_only(new: dict[str, object], expected: list[str]) -> None:
    old = ConnectionParams.model_validate(PG)
    assert endpoint_changes("postgres", old, ConnectionParams.model_validate(new)) == expected


def test_an_unknown_engine_treats_every_param_as_its_endpoint() -> None:
    old = ConnectionParams.model_validate({"prefix": "a"})
    assert endpoint_changes("db2", old, ConnectionParams.model_validate({"prefix": "b"})) == [
        "prefix"
    ]


def test_changed_fields_are_names_never_values() -> None:
    current: Connection = make_connection("c_1", roles=["source"], params=PG)
    patch = ConnectionPatch(
        name="renamed",
        params={**PG, "host": "evil.example", "options": {"sslmode": "require"}},
        credentials={"username": "u", "password": "pw-never-shown"},
        tags=["uat"],
    )
    names = changed_fields(current, patch)
    assert names == [
        "credentials",
        "name",
        "params.host",
        "params.options.sslmode",
        "tags",
    ]
    assert "evil" not in repr(names) and "pw-never" not in repr(names)
