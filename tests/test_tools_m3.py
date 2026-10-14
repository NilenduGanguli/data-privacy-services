"""The M3 operator tools (offline): ``smoke_test.py --m3`` and ``bench_connector.py --engine``.

The live parts run against the stack (``python tools/smoke_test.py --m3``, ``python
tools/bench_connector.py --engine oracle|mongo``); these tests pin their arguments, the demo
connections and members, the run specs they submit and the verdicts they print — never a value.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tools import bench_connector, m2_api, smoke_test

KEY: dict[str, Any] = {"kid": "k_smoke", "dek": "never-printed"}


def test_smoke_takes_an_m3_flag() -> None:
    assert smoke_test.parse_args([]).m3 is False
    assert smoke_test.parse_args(["--m3"]).m3 is True
    with pytest.raises(SystemExit):
        smoke_test.parse_args(["--m4"])


def test_m3_demo_connections_use_the_compose_secret_refs() -> None:
    conns = m2_api.M3_CONNECTIONS
    assert set(conns) == {"smoke-ora-src", "smoke-ora-tgt", "smoke-mdb-src", "smoke-mdb-tgt"}
    assert {n: c["secret_ref"] for n, c in conns.items()} == {
        "smoke-ora-src": "dps/src/oracle-crm-ro",
        "smoke-ora-tgt": "dps/tgt/oracle-crm-masked",
        "smoke-mdb-src": "dps/src/mongo-crm",
        "smoke-mdb-tgt": "dps/tgt/mongo-crm-uat",
    }
    assert conns["smoke-ora-src"]["params"] == {
        "host": "oracle",
        "port": 1521,
        "service_name": "FREEPDB1",
    }
    assert conns["smoke-mdb-src"]["params"] == {"database": "crm"}
    assert conns["smoke-mdb-tgt"]["params"] == {"database": "crm_uat"}
    assert all(c["roles"] == [n.rsplit("-", 1)[1].replace("src", "source").replace("tgt", "target")]
               for n, c in conns.items())  # fmt: skip
    # the shared console harness registers the same names against the same refs
    from tests.console.m3_console import CONNECTIONS as CONSOLE

    for name, spec in conns.items():
        assert CONSOLE[name][:3] == (spec["engine"], spec["params"], spec["secret_ref"]), name


def test_m3_members_are_the_three_engine_demo_newest_oracle() -> None:
    members = smoke_test.M3_MEMBERS
    assert [m.engine for m in members] == ["postgres", "mongo", "oracle"]  # Oracle anchors
    by = {m.engine: m for m in members}
    assert (by["postgres"].source, by["postgres"].target) == ("bank-pg-ro", "bank-uat")
    assert by["postgres"].obj == {"owner": "bank", "name": "customers"}
    assert by["postgres"].target_obj == {"owner": "bank_uat", "name": "customers_smoke"}
    assert by["mongo"].obj == {"owner": None, "name": "profiles"}
    assert by["mongo"].target_obj == {"owner": None, "name": "profiles_smoke"}
    assert by["oracle"].obj == {"owner": "CRM", "name": "CUSTOMERS"}
    assert by["oracle"].target_obj == {"owner": "CRM_MASKED", "name": "CUSTOMERS_SMOKE"}


def test_m3_run_spec_is_a_grouped_table_run_under_the_key() -> None:
    member = smoke_test.M3_MEMBERS[2]
    spec = smoke_test.m3_run_spec(member, {"smoke-ora-src": "c1", "smoke-ora-tgt": "c2"},
                                  "s_1", "grp_1", KEY)  # fmt: skip
    assert spec["mode"] == "obfuscate" and spec["snapshot_id"] == "s_1"
    assert spec["group_id"] == "grp_1" and spec["purpose"] == smoke_test.PURPOSE
    assert spec["source"] == {"connection_id": "c1", "object": member.obj, "parallelism": 2}
    assert spec["target"] == {
        "write_mode": "table",
        "connection_id": "c2",
        "object": member.target_obj,
        "create_if_missing": True,
        "table_load": "truncate_insert",
    }
    assert spec["consistency"] == {"scope": "key", "key_material": KEY}


def test_consistency_verdict_needs_three_consistent_pairs() -> None:
    ok = {
        "passed": True,
        "consistent_pairs": 3,
        "total_pairs": 3,
        "problems": [],
        "pairs": [{"coverage": 1.0}, {"coverage": 1.0}, {"coverage": 0.999}],
        "sampled": 500,
    }
    passed, detail = smoke_test.consistency_verdict(ok)
    assert passed and detail == "3/3 pairs consistent, 500 sampled, min coverage 0.999"
    assert not smoke_test.consistency_verdict({**ok, "passed": False})[0]
    assert not smoke_test.consistency_verdict({**ok, "consistent_pairs": 2})[0]
    assert not smoke_test.consistency_verdict({**ok, "total_pairs": 2, "consistent_pairs": 2})[0]
    bad, why = smoke_test.consistency_verdict(
        {**ok, "passed": False, "problems": [{"code": "method_mismatch", "member": 1}]}
    )
    assert not bad and "method_mismatch" in why
    assert smoke_test.consistency_verdict({}) == (False, "0/0 pairs consistent, 0 sampled")


def test_bench_takes_an_engine() -> None:
    assert bench_connector.parse_args([]).engine == "postgres"
    args = bench_connector.parse_args(["--engine", "oracle", "--rows", "20000"])
    assert (args.engine, args.rows, args.write_mode) == ("oracle", 20000, "table")
    assert bench_connector.parse_args(["--engine", "mongo"]).engine == "mongo"
    for bad in (["--engine", "db2"], ["--engine", "oracle", "--write-mode", "in_place"],
                ["--engine", "mongo", "--write-mode", "files"]):  # fmt: skip
        with pytest.raises(SystemExit):
            bench_connector.parse_args(bad)


def test_bench_result_path_names_the_engine() -> None:
    when = datetime(2026, 10, 14, tzinfo=UTC)
    m2 = bench_connector.result_path(Path("o"), "obfuscate", "table", "1-worker", when)
    assert m2 == Path("o/m2-2026-10-14-obfuscate-table-1-worker.json")
    m3 = bench_connector.result_path(
        Path("o"), "obfuscate", "table", "1-worker", when, engine="oracle"
    )
    assert m3 == Path("o/m3-2026-10-14-oracle-obfuscate-table-1-worker.json")


def test_bench_m3_spec_reads_the_demo_source_into_a_scratch_target() -> None:
    ids = {"smoke-ora-src": "o1", "smoke-ora-tgt": "o2", "smoke-mdb-src": "m1",
           "smoke-mdb-tgt": "m2"}  # fmt: skip
    args = bench_connector.parse_args(["--engine", "oracle", "--rows", "20000", "--batch", "1000"])
    spec = bench_connector._spec(args, ids, "s_1")
    assert spec["source"]["connection_id"] == "o1"
    assert spec["source"]["object"] == {"owner": "CRM", "name": "CUSTOMERS"}
    assert spec["source"]["batch_rows"] == 1000
    assert spec["target"] == {
        "write_mode": "table",
        "connection_id": "o2",
        "object": {"owner": "CRM_MASKED", "name": "CUSTOMERS_BENCH"},
        "table_load": "truncate_insert",
        "create_if_missing": True,
    }
    args = bench_connector.parse_args(["--engine", "mongo"])
    spec = bench_connector._spec(args, ids, "s_2")
    assert spec["source"]["object"] == {"owner": None, "name": "profiles"}
    assert spec["target"]["connection_id"] == "m2"
    assert spec["target"]["object"] == {"owner": None, "name": "profiles_bench"}
    assert spec["policy"] == bench_connector.M3_POLICY == "gdpr-pseudonymise"
