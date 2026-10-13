"""The demo data generator (offline): deterministic, rule-valid fakes only, consistent keys."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from stdnum import luhn
from stdnum.mx import curp

from dps.engine.detect.checksums import verhoeff_check_digit
from dps.engine.synth.surrogates import RFC2606_DOMAINS, TEST_CARD_IIN
from tools.seed_sources import (
    TABLE_DDL,
    TABLES,
    columns_of,
    generate_rows,
    main,
    parse_args,
)

INITDB = Path(__file__).resolve().parents[1] / "docker/source-pg/initdb/01_bank_schema.sql"


@pytest.fixture(scope="module")
def data() -> dict[str, list[dict[str, object]]]:
    return generate_rows(rows=50, seed=7)


def test_ten_tables_with_the_documented_ratios(data) -> None:  # noqa: ANN001
    assert tuple(data) == TABLES and len(TABLES) == 10
    n = 50
    counts = {t: len(rows) for t, rows in data.items()}
    assert counts["customers"] == n and counts["employees"] == 50
    assert n <= counts["addresses"] <= int(n * 1.3) and counts["contacts"] == 2 * n
    assert counts["accounts"] == n + n // 2 and counts["cards"] == n + n // 5
    assert counts["transactions"] == 10 * n and counts["loans"] == n // 4
    assert counts["loans"] <= counts["loan_applicants"] <= 2 * counts["loans"]
    assert counts["kyc_documents"] == n
    for table, rows in data.items():
        assert all(set(r) == set(columns_of(table)) for r in rows), table


def test_the_same_seed_gives_the_same_rows(data) -> None:  # noqa: ANN001
    assert generate_rows(rows=50, seed=7) == data
    other = generate_rows(rows=50, seed=8)
    assert other["customers"] != data["customers"]
    assert {c["customer_id"] for c in other["customers"]} == {
        c["customer_id"] for c in data["customers"]
    }


def test_identifiers_are_rule_valid_fakes(data) -> None:  # noqa: ANN001
    formats = {
        "AADHAAR": re.compile(r"^[2-9]\d{11}$|^1\d{11}$"),
        "SSN": re.compile(r"^9\d{2}-\d{2}-\d{4}$"),  # the 9xx area is never issued as an SSN
        "NINO": re.compile(r"^QQ\d{6}[A-D]$"),  # HMRC's example prefix, never issued
        "CURP": re.compile(r"^[A-Z]{4}\d{6}[HM][A-Z]{5}[0-9A-Z]\d$"),
    }
    seen = set()
    for c in data["customers"]:
        kind, value = c["id_type"], str(c["national_id"])
        seen.add(kind)
        assert formats[kind].fullmatch(value), kind
        if kind == "AADHAAR":
            assert value[0] == "1"  # UIDAI never issues numbers starting with 0 or 1
            assert verhoeff_check_digit(value[:-1]) == value[-1]
        if kind == "CURP":
            assert curp.is_valid(value)
        if c["country_code"] == "IN":
            assert re.fullmatch(r"[A-Z]{3}P[A-Z]\d{4}[A-Z]", str(c["tax_id"]))
        else:
            assert c["tax_id"] is None
        domain = str(c["email"]).rsplit("@", 1)[1]
        assert domain in RFC2606_DOMAINS
        assert 10**7 <= int(c["customer_id"]) < 10**8  # 8 digits
    assert seen >= {"AADHAAR", "SSN"}
    for card in data["cards"]:
        pan = str(card["pan"])
        assert pan.startswith(TEST_CARD_IIN) and luhn.is_valid(pan)
        assert re.fullmatch(r"\d{2}/\d{2}", str(card["expiry"]))
    notes = [c["notes"] for c in data["customers"] if c["notes"]]
    assert notes and all("@" in str(n) for n in notes)  # free text with an e-mail in it


def test_foreign_keys_line_up(data) -> None:  # noqa: ANN001
    customers = {c["customer_id"] for c in data["customers"]}
    employees = {e["employee_id"] for e in data["employees"]}
    accounts = {a["account_id"] for a in data["accounts"]}
    loans = {loan["loan_id"] for loan in data["loans"]}
    assert all(c["relationship_manager_id"] in employees for c in data["customers"])
    for table in ("addresses", "contacts", "accounts", "loans", "kyc_documents"):
        assert all(r["customer_id"] in customers for r in data[table]), table
    assert all(r["account_id"] in accounts for r in data["cards"])
    assert all(r["account_id"] in accounts for r in data["transactions"])
    assert all(r["loan_id"] in loans for r in data["loan_applicants"])
    assert all(
        r["customer_id"] is None or r["customer_id"] in customers for r in data["loan_applicants"]
    )
    assert len({a["account_number"] for a in data["accounts"]}) == len(data["accounts"])
    for table in TABLES:
        key = columns_of(table)[0]
        assert len({r[key] for r in data[table]}) == len(data[table]), table


def _normalise(sql: str) -> str:
    sql = re.sub(r"--[^\n]*", "", sql)
    return re.sub(r"\s+", " ", sql).strip().rstrip(",")


def test_table_ddl_matches_the_initdb_schema() -> None:
    text = INITDB.read_text("utf-8")
    for table in TABLES:
        match = re.search(rf"CREATE TABLE bank\.{table} \((.*?)\);\n", text, re.S)
        assert match is not None, table
        assert _normalise(match.group(1)) == _normalise(TABLE_DDL[table].replace("{s}", "bank"))


def test_cli_arguments_and_unsupported_targets(capsys: pytest.CaptureFixture[str]) -> None:
    args = parse_args(["--target", "postgres"])
    assert (args.schema, args.rows, args.seed, args.restore_copy, args.reset) == (
        "bank",
        20_000,
        7,
        False,
        False,
    )
    assert parse_args(["--target", "all"]).target == "all"
    # M3: oracle/mongo targets exist; a failure names the exception class, never the endpoint
    bad = ["--target", "oracle", "--oracle-dsn", "127.0.0.1:1/NOPE", "--rows", "1"]
    assert main(bad) == 1
    err = capsys.readouterr().err
    assert "seeding oracle failed" in err and "127.0.0.1" not in err
    with pytest.raises(SystemExit):
        parse_args(["--target", "postgres", "--schema", "Bad-Name"])
