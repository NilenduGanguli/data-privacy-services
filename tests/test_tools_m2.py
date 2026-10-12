"""The M2 operator tools (offline): benchmark arguments and summary, the demo's table order, and a
local reveal of an obfuscated row with the caller's key."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from dps.config import Settings
from dps.engine.batch import BatchTransformer, ColumnPlan
from dps.engine.policy import MethodSpec, load_preset
from dps.engine.service import PrivacyEngine
from dps.engine.transform import fpe
from dps.engine.transform.crypto import generate_key_material
from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape
from tools import bench_connector, demo_m2, reveal_rows

CONTRACT_TABLES = {
    "customers",
    "accounts",
    "cards",
    "transactions",
    "loans",
    "loan_applicants",
    "addresses",
    "contacts",
    "employees",
    "kyc_documents",
}


def test_bench_arguments_and_result_path() -> None:
    args = bench_connector.parse_args([])
    assert (args.mode, args.write_mode, args.parallelism) == ("obfuscate", "table", 4)
    assert (args.rows, args.batch, args.partition_rows, args.label) == (
        200_000,
        1000,
        50_000,
        "1-worker",
    )
    with pytest.raises(SystemExit):
        bench_connector.parse_args(["--write-mode", "in_place", "--mode", "synthesize"])
    path = bench_connector.result_path(
        Path("out"), "obfuscate", "files", "3-workers", datetime(2026, 10, 12, tzinfo=UTC)
    )
    assert path == Path("out/m2-2026-10-12-obfuscate-files-3-workers.json")


def test_bench_summary_is_rows_over_wall_time() -> None:
    job = {
        "progress": {
            "rows_written": 1000,
            "values_by_method": {"fpe_digits": 900, "col:customer_id": 1000, "keep": 50},
        }
    }
    parts: list[dict[str, Any]] = [
        {"rows_per_second": 10.0},
        {"rows_per_second": 20.0},
        {"rows_per_second": 30.0},
        {"rows_per_second": 40.0},
        {"rows_per_second": None},
    ]
    out = bench_connector.summarise(job, parts, 10.0, 20.0, mode="obfuscate", label="x")
    assert out["rows"] == 1000 and out["wall_seconds"] == 10.0 and out["rows_per_second"] == 100.0
    assert out["partition_rows_per_second"] == {"p50": 25.0, "p95": 38.5}
    assert out["partitions"] == 5 and out["mode"] == "obfuscate" and out["label"] == "x"
    assert out["values_by_method"] == {"fpe_digits": 900, "keep": 50}  # no per-column counters
    empty = bench_connector.summarise({"progress": {}}, [], 0.0, 0.0)
    assert empty["rows_per_second"] == 0.0 and empty["partition_rows_per_second"]["p50"] == 0.0


def test_demo_tables_are_the_contract_ten_parents_first() -> None:
    assert set(demo_m2.TABLES) == CONTRACT_TABLES and len(demo_m2.TABLES) == 10
    order = {t: i for i, t in enumerate(demo_m2.TABLES)}
    for child, _column, parent, _key in demo_m2.FOREIGN_KEYS:
        assert order[parent] < order[child], (parent, child)
    assert set(demo_m2.KEYS) == CONTRACT_TABLES


COLS = [
    ColumnInfo(
        path="$.customer_id", name="customer_id", data_type="bigint", kind="int", is_key=True
    ),
    ColumnInfo(
        path="$.full_name", name="full_name", data_type="varchar(120)", kind="text", length=120
    ),
    ColumnInfo(path="$.email", name="email", data_type="varchar(254)", kind="text", length=254),
    ColumnInfo(path="$.dob", name="dob", data_type="date", kind="date"),
    ColumnInfo(path="$.segment", name="segment", data_type="text", kind="text"),
]
SHAPE = TableShape(
    object=ObjectRef(owner="bank", name="customers"),
    columns=COLS,
    key=["customer_id"],
    key_source="primary_key",
)


def _plan(name: str, entity: str, method: str, **params: Any) -> ColumnPlan:
    col = SHAPE.column(name)
    return ColumnPlan(
        path=col.path,
        name=name,
        entity_type=entity,
        method=MethodSpec(method=method, **params),
        text_column=False,
        reversible=True,
        kind=col.kind,
        length=col.length,
    )


def test_reveal_rows_restores_the_original_row(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    if not fpe.fpe_available():
        pytest.skip("the [fpe] extra is not installed")
    engine = PrivacyEngine(
        Settings(_env_file=None, llm_provider="stub", ner_enabled=False, fpe_enabled=True)
    )
    plans = [
        _plan("customer_id", "CUSTOMER_ID", "fpe_digits"),
        _plan("full_name", "PERSON_NAME", "fpe_text"),
        _plan("email", "EMAIL", "email"),
        _plan("dob", "DATE_OF_BIRTH", "date_shift", window_days=365),
    ]
    key = generate_key_material("key")
    policy = load_preset("gdpr-pseudonymise")
    transformer = BatchTransformer(
        engine,
        policy=policy,
        mode="obfuscate",
        columns=plans,
        shape=SHAPE,
        key=key,
        seed=None,
        locale="en_US",
        anchor=("customer_id", "CUSTOMER_ID"),
    )
    original = {
        "customer_id": 10_000_042,
        "full_name": "Ananya Krishnan",
        "email": "ananya.k@example-mail.in",
        "dob": date(1988, 3, 14),
        "segment": "retail",
    }
    [masked] = transformer.transform([original]).rows
    assert masked["full_name"] != original["full_name"] and masked["dob"] != original["dob"]
    reveal_map = [
        {
            "path": p.path,
            "entity_type": p.entity_type,
            "method": p.method.method,
            "params": p.method.params,
            "anchor_path": "$.customer_id",
            "reveal": True,
            "text_column": False,
        }
        for p in plans
    ]
    files = {
        "evidence": {"pack": {"reveal_map": reveal_map, "consistency": {"kid": key.kid}}},
        "snapshot": {
            "shape": SHAPE.model_dump(mode="json"),
            "columns": [{"name": "customer_id", "entity_type": "CUSTOMER_ID", "anchor": True}],
            "policy_name": "gdpr-pseudonymise",
        },
        "key": key.model_dump(mode="json"),
    }
    for name, doc in files.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(doc), encoding="utf-8")
    (tmp_path / "rows.jsonl").write_text(json.dumps(masked, default=str) + "\n\n", "utf-8")
    argv = [
        "--evidence",
        str(tmp_path / "evidence.json"),
        "--snapshot",
        str(tmp_path / "snapshot.json"),
        "--key",
        str(tmp_path / "key.json"),
        "--rows",
        str(tmp_path / "rows.jsonl"),
    ]
    assert reveal_rows.main(argv) == 0
    captured = capsys.readouterr()
    [line] = captured.out.splitlines()
    assert json.loads(line) == {**original, "dob": "1988-03-14"}
    assert "revealed 1 rows" in captured.err
    other = generate_key_material("key").model_dump(mode="json")
    (tmp_path / "key.json").write_text(json.dumps(other), encoding="utf-8")
    assert reveal_rows.main(argv) == 1  # another key never reveals silently
    refused = capsys.readouterr()
    assert refused.out == "" and "reveal refused" in refused.err
    assert (
        original["full_name"] not in refused.err and str(original["customer_id"]) not in refused.err
    )
