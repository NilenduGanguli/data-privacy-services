from __future__ import annotations

import itertools
import random
import re
from typing import Any

import pytest

from dps.layers.connectors import oracle_sql as osql
from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape


def _cat(name: str, data_type: str, **kw: Any) -> osql.CatalogColumn:
    base: dict[str, Any] = dict(
        data_length=22,
        char_length=0,
        char_used=None,
        data_precision=None,
        data_scale=None,
        nullable=True,
        has_default=False,
        is_identity=False,
    )
    base.update(kw)
    return osql.CatalogColumn(name=name, data_type=data_type, **base)


def _shape(*cols: ColumnInfo, key: list[str], src: str = "primary_key") -> TableShape:
    return TableShape(
        object=ObjectRef(owner="CRM", name="INTERACTIONS"),
        columns=list(cols),
        key=key,
        key_source=src,  # type: ignore[arg-type]
    )


CID = osql.column_info(
    _cat("CUSTOMER_ID", "NUMBER", data_precision=12, data_scale=0, nullable=False), is_key=True
)
TS9 = osql.column_info(_cat("EVENT_TS", "TIMESTAMP(9)", data_scale=9, nullable=False), is_key=True)
REF = osql.column_info(
    _cat("DOC_REF", "CHAR", data_length=12, char_length=12, char_used="B"), is_key=True
)
NOTES = osql.column_info(_cat("NOTES", "CLOB", data_length=4000), is_key=False)
TZ = osql.column_info(_cat("SEEN_AT", "TIMESTAMP(6) WITH TIME ZONE", data_scale=6), is_key=False)
NAME = osql.column_info(
    _cat("FULL_NAME", "VARCHAR2", data_length=480, char_length=120, char_used="C"), is_key=False
)


@pytest.mark.parametrize(
    "cat,kind,data_type,ts,length",
    [
        (_cat("A", "NUMBER", data_precision=12, data_scale=0), "int", "NUMBER(12)", None, None),
        (
            _cat("A", "NUMBER", data_precision=14, data_scale=2),
            "decimal",
            "NUMBER(14,2)",
            None,
            None,
        ),
        (_cat("A", "NUMBER"), "decimal", "NUMBER", None, None),
        (_cat("A", "NUMBER", data_scale=0), "int", "NUMBER(38)", None, None),
        (_cat("A", "FLOAT", data_precision=126), "float", "FLOAT(126)", None, None),
        (
            _cat("A", "VARCHAR2", data_length=480, char_length=120, char_used="C"),
            "text",
            "VARCHAR2(120 CHAR)",
            None,
            120,
        ),
        (
            _cat("A", "VARCHAR2", data_length=16, char_length=16, char_used="B"),
            "text",
            "VARCHAR2(16)",
            None,
            16,
        ),
        (
            _cat("A", "CHAR", data_length=10, char_length=10, char_used="B"),
            "char",
            "CHAR(10)",
            None,
            10,
        ),
        (_cat("A", "DATE"), "timestamp", "DATE", 0, None),
        (_cat("A", "TIMESTAMP(9)", data_scale=9), "timestamp", "TIMESTAMP(9)", 9, None),
        (
            _cat("A", "TIMESTAMP(6) WITH TIME ZONE", data_scale=6),
            "timestamp",
            "TIMESTAMP(6) WITH TIME ZONE",
            6,
            None,
        ),
        (_cat("A", "CLOB"), "lob_text", "CLOB", None, None),
        (_cat("A", "BLOB"), "lob_binary", "BLOB", None, None),
        (_cat("A", "RAW", data_length=16), "binary", "RAW(16)", None, 16),
        (_cat("A", "XMLTYPE"), "other", "XMLTYPE", None, None),
        (
            _cat("A", "TIMESTAMP(6) WITH LOCAL TIME ZONE"),
            "other",
            "TIMESTAMP(6) WITH LOCAL TIME ZONE",
            None,
            None,
        ),
    ],
)
def test_type_table(
    cat: osql.CatalogColumn, kind: str, data_type: str, ts: int | None, length: int | None
) -> None:
    info = osql.column_info(cat, is_key=False)
    assert (info.kind, info.data_type, info.ts_precision, info.length) == (
        kind,
        data_type,
        ts,
        length,
    )
    assert info.path == "$.A" and info.pseudo is False


def test_column_paths_quote_names_that_are_not_plain_identifiers() -> None:
    assert osql.column_path("FULL_NAME") == "$.FULL_NAME"
    assert osql.column_path("A$B") == "$['A$B']" and osql.column_path("N#") == "$['N#']"


def _py(expr: str) -> str:
    return expr.replace(" AND ", " and ").replace(" OR ", " or ").replace(" = ", " == ")


@pytest.mark.parametrize("n", [1, 2, 3])
@pytest.mark.parametrize("op", [">", ">=", "<"])
def test_tuple_predicate_is_lexicographic_over_a_domain(n: int, op: str) -> None:
    cols, vals = ["a", "b", "c"][:n], ["x", "y", "z"][:n]
    expr = _py(osql.tuple_predicate(cols, vals, op))  # type: ignore[arg-type]
    domain = list(itertools.product(range(3), repeat=n))
    for left, right in itertools.product(domain, domain):
        env = dict(zip(cols, left, strict=True)) | dict(zip(vals, right, strict=True))
        expected = {">": left > right, ">=": left >= right, "<": left < right}[op]
        # Test oracle: evaluates a predicate built by the code under test from the fixed names
        # above (no external input) against every pair of the 3-valued domain.
        assert eval(expr, {}, env) is expected, (expr, left, right)  # noqa: S307


def test_tuple_predicate_needs_one_value_per_column() -> None:
    with pytest.raises(ValueError):
        osql.tuple_predicate(["a", "b"], ["x"], ">")
    with pytest.raises(ValueError):
        osql.tuple_predicate([], [], ">")


def test_read_sql_keyset_shape_and_pitfall_rules() -> None:
    shape = _shape(CID, TS9, NOTES, TZ, key=["CUSTOMER_ID", "EVENT_TS"])
    plan = osql.read_sql(
        shape,
        columns=shape.columns,
        where="CHANNEL = :ch",
        query=None,
        sample=None,
        lower=True,
        upper=True,
        after=True,
    )
    sql = plan.sql
    assert "FOR UPDATE" not in sql.upper() and sql.endswith("FETCH FIRST :batch ROWS ONLY")
    assert f"TO_CHAR(t.\"EVENT_TS\", '{osql.TS9_FORMAT}')" in sql  # ns survive the driver
    assert f"TO_TIMESTAMP(:af1, '{osql.TS9_FORMAT}')" in sql  # and go back as text
    assert 'ORDER BY t."CUSTOMER_ID", t."EVENT_TS"' in sql  # raw columns, never aliases
    assert not re.search(r'AS\s+"(CUSTOMER_ID|EVENT_TS)"', sql)
    assert f"TO_CHAR(t.\"SEEN_AT\", '{osql.TSTZ_FORMAT}')" in sql
    assert "(CHANNEL = :ch)" in sql and ":lo0" in sql and ":hi1" in sql
    assert [c.name for c in plan.columns] == ["CUSTOMER_ID", "EVENT_TS", "NOTES", "SEEN_AT"]


def test_read_sql_over_a_query_and_a_seeded_sample() -> None:
    shape = _shape(CID, NAME, key=["CUSTOMER_ID"])
    over_query = osql.read_sql(
        shape,
        columns=shape.columns,
        where=None,
        query='SELECT "CUSTOMER_ID", "FULL_NAME" FROM "CRM"."CUSTOMERS"',
        sample=None,
        lower=False,
        upper=False,
        after=False,
    ).sql
    assert (
        'FROM (SELECT "CUSTOMER_ID", "FULL_NAME" FROM "CRM"."CUSTOMERS") t ORDER BY' in over_query
    )
    sampled = osql.read_sql(
        shape,
        columns=shape.columns,
        where=None,
        query=None,
        sample=(12.5, 7),
        lower=False,
        upper=False,
        after=False,
    ).sql
    assert 'FROM "CRM"."INTERACTIONS" SAMPLE (12.500000) SEED (7) t ORDER BY' in sampled
    count = osql.count_sql(shape, where="SEGMENT = :s", query=None, sample=None)
    assert (
        count == 'SELECT /* dps:count */ COUNT(*) FROM "CRM"."INTERACTIONS" t WHERE (SEGMENT = :s)'
    )


def test_sample_sql_spreads_keys_over_the_set_or_draws_a_fresh_sample() -> None:
    shape = _shape(CID, NAME, key=["CUSTOMER_ID"])
    keys = osql.sample_sql(
        shape, columns=shape.columns, where="SEGMENT = :s", query=None, percent=5.0, keys_only=True
    )
    assert [c.name for c in keys.columns] == ["CUSTOMER_ID"]
    # the first key of every run of ceil(total / n) keys of the ordered set (quantiles, as M2's
    # PostgreSQL sample_keys), never the lowest n keys: a FETCH FIRST over ORDER BY key would cut
    # every partition boundary from the bottom of the key space
    assert keys.sql == (
        'SELECT /* dps:sample */ s.DPS_K0 FROM (SELECT t."CUSTOMER_ID" DPS_K0, '
        'ROW_NUMBER() OVER (ORDER BY t."CUSTOMER_ID") DPS_RN, COUNT(*) OVER () DPS_N '
        'FROM "CRM"."INTERACTIONS" SAMPLE (5.000000) t WHERE (SEGMENT = :s)) s '
        "WHERE MOD(s.DPS_RN - 1, CEIL(s.DPS_N / GREATEST(:n, 1))) = 0 ORDER BY s.DPS_RN"
    )
    assert "FETCH FIRST" not in keys.sql and keys.bind_types == {}
    rows = osql.sample_sql(shape, columns=shape.columns, where=None, query=None, percent=None)
    assert "SAMPLE" not in rows.sql and "ORDER BY" not in rows.sql
    assert rows.sql.endswith("FETCH FIRST :n ROWS ONLY")


def test_key_sample_below_an_upper_bound_is_spread_below_it() -> None:
    ts6 = osql.column_info(_cat("OCCURRED_AT", "TIMESTAMP(6)", data_scale=6), is_key=True)
    shape = _shape(REF, ts6, NAME, key=["DOC_REF", "OCCURRED_AT"])
    plan = osql.sample_sql(
        shape,
        columns=shape.columns,
        where=None,
        query=None,
        percent=None,
        keys_only=True,
        upper=True,
    )
    inner = plan.sql.split(") s WHERE")[0]
    assert (
        'WHERE (t."DOC_REF" <= :hi0 AND (t."DOC_REF" < :hi0 OR (t."DOC_REF" = :hi0 AND '
        't."OCCURRED_AT" < :hi1)))' in inner
    )  # the bound filters the set the quantiles are taken from, not the sampled keys
    assert plan.bind_types == {"hi0": "char", "hi1": "timestamp"}
    rid = osql.rowid_column()
    by_rowid = osql.sample_sql(
        _shape(NAME, rid, key=["ROWID"], src="rowid"),
        columns=[NAME, rid],
        where=None,
        query=None,
        percent=None,
        keys_only=True,
        upper=True,
    ).sql
    assert "ROWIDTOCHAR(t.ROWID) DPS_K0" in by_rowid
    assert "ORDER BY t.ROWID) DPS_RN" in by_rowid and "t.ROWID < CHARTOROWID(:hi0)" in by_rowid


@pytest.mark.parametrize(
    "data_type,expected",
    [
        ("TIMESTAMP(0)", "timestamp"),
        ("TIMESTAMP(3)", "timestamp"),
        ("TIMESTAMP(6)", "timestamp"),
        ("TIMESTAMP(9)", None),  # FF9 text both ways
        ("TIMESTAMP(6) WITH TIME ZONE", None),  # FF9+offset text
        ("DATE", None),  # the driver's DATE bind is exact for DATE
    ],
)
def test_timestamps_up_to_microseconds_bind_as_timestamp(
    data_type: str, expected: str | None
) -> None:
    # python-oracledb thin binds a datetime as DATE, which drops the fractional seconds: a
    # TIMESTAMP(1-6) write loses its microseconds and a key bound compared at second precision
    # re-reads the same rows forever (measured on the lane's Oracle 23.26).
    col = osql.column_info(_cat("A", data_type), is_key=True)
    assert osql.input_type(col) == expected


def test_timestamp6_key_bounds_are_typed() -> None:
    ts6 = osql.column_info(_cat("OCCURRED_AT", "TIMESTAMP(6)", data_scale=6), is_key=True)
    shape = _shape(CID, ts6, NAME, key=["CUSTOMER_ID", "OCCURRED_AT"])
    plan = osql.read_sql(
        shape,
        columns=shape.columns,
        where=None,
        query=None,
        sample=None,
        lower=True,
        upper=True,
        after=True,
    )
    assert plan.bind_types == {"lo1": "timestamp", "hi1": "timestamp", "af1": "timestamp"}
    assert 't."OCCURRED_AT" > :af1' in plan.sql  # a typed bind, not text


def test_char_keys_bind_as_char_and_are_padded() -> None:
    shape = _shape(REF, key=["DOC_REF"])
    plan = osql.read_sql(
        shape,
        columns=shape.columns,
        where=None,
        query=None,
        sample=None,
        lower=False,
        upper=False,
        after=True,
    )
    assert plan.bind_types == {"af0": "char"}
    assert osql.pad_char("K17", 12) == "K17         " and osql.pad_char(None, 12) is None
    assert osql.pad_char("K17         ", 12) == "K17         "


def test_rowid_tables_page_on_rowid() -> None:
    rid = osql.rowid_column()
    assert rid.pseudo and rid.is_key and rid.name == "ROWID" and rid.kind == "int"
    shape = _shape(NAME, rid, key=["ROWID"], src="rowid")
    sql = osql.read_sql(
        shape,
        columns=shape.columns,
        where=None,
        query=None,
        sample=None,
        lower=True,
        upper=False,
        after=False,
    ).sql
    assert "ROWIDTOCHAR(t.ROWID)" in sql and "t.ROWID >= CHARTOROWID(:lo0)" in sql
    assert sql.split("ORDER BY")[1].strip().startswith("t.ROWID")


def test_rowid_codec_round_trip_and_order() -> None:
    rng = random.Random(7)
    comps = [
        (rng.randrange(2**32), rng.randrange(1024), rng.randrange(2**22), rng.randrange(2**16))
        for _ in range(500)
    ]
    rowids = []
    for obj, fno, blk, row in comps:
        rid = osql._enc(obj, 6) + osql._enc(fno, 3) + osql._enc(blk, 6) + osql._enc(row, 3)
        assert osql.key_to_rowid(osql.rowid_to_key(rid)) == rid
        rowids.append(rid)
    by_components = [r for _, r in sorted(zip(comps, rowids, strict=True))]
    assert sorted(rowids, key=osql.rowid_to_key) == by_components
    with pytest.raises(ValueError):
        osql.rowid_to_key("*BAEADAoA")  # logical (IOT) rowids are not range-able


def test_write_statements() -> None:
    target = ObjectRef(owner="CRM_MASKED", name="CUSTOMERS")
    merge = osql.merge_sql(target, [CID, NAME, NOTES], ["CUSTOMER_ID"])
    assert merge.startswith('MERGE /* dps:merge */ INTO "CRM_MASKED"."CUSTOMERS" d USING (SELECT')
    assert 'ON (d."CUSTOMER_ID" = s."CUSTOMER_ID")' in merge
    assert "WHEN MATCHED THEN UPDATE SET" in merge
    assert 'd."CUSTOMER_ID" = s."CUSTOMER_ID",' not in merge.split("UPDATE SET")[1]
    keys_only = osql.merge_sql(target, [CID], ["CUSTOMER_ID"])
    assert "WHEN MATCHED" not in keys_only and "WHEN NOT MATCHED THEN INSERT" in keys_only
    upd = osql.update_sql(ObjectRef(owner="CRM", name="CUSTOMERS"), [NAME, NOTES], [CID])
    assert upd.split(" WHERE ")[0].count('"CUSTOMER_ID"') == 0
    assert upd.endswith('d."CUSTOMER_ID" = :v2')
    ins = osql.insert_sql(target, [CID, NAME], sequence=("CUSTOMER_ID", '"CRM"."CASE_SEQ"'))
    assert '"CRM"."CASE_SEQ".NEXTVAL' in ins and ":v0" not in ins.split("VALUES")[1].split(",")[0]
    for stmt in (
        merge,
        keys_only,
        upd,
        ins,
        osql.collision_sql(target, [CID], 3),
        osql.collision_sql(target, [CID, TS9], 2),
        osql.ledger_ddl("CRM"),
        osql.ledger_ensure_sql("CRM"),
        osql.ledger_mark_sql("CRM"),
        osql.ledger_read_sql("CRM"),
        osql.ledger_purge_sql("CRM"),
    ):
        assert "FOR UPDATE" not in stmt.upper() and "FETCH FIRST" not in stmt.upper()


def test_collision_sql_lists_one_bind_per_candidate_key() -> None:
    target = ObjectRef(owner="CRM_MASKED", name="CUSTOMERS")
    single = osql.collision_sql(target, [CID], 3)
    assert single.endswith('WHERE d."CUSTOMER_ID" IN (:c0, :c1, :c2)')
    pair = osql.collision_sql(target, [CID, TS9], 2)
    assert 'WHERE (d."CUSTOMER_ID", d."EVENT_TS") IN ((:c0_0, ' in pair
    assert f"TO_TIMESTAMP(:c1_1, '{osql.TS9_FORMAT}'))" in pair


def test_ledger_statements_are_19c_safe_and_fenced() -> None:
    ddl = osql.ledger_ddl("CRM")
    assert "SQLCODE != -955" in ddl and '"CRM"."DPS_WRITE_LEDGER"' in ddl
    assert "ROWS_WRITTEN" in ddl  # LedgerMark.rows: a resume reconciles the partition counters
    # The partition's row is created at batch 0 before it is locked (contract §0 amendment
    # 2026-10-12): a concurrent first batch waits on the other holder's insert.
    ensure = osql.ledger_ensure_sql("CRM")
    assert "WHEN NOT MATCHED THEN INSERT" in ensure and "WHEN MATCHED" not in ensure
    assert "VALUES (s.JOB_ID, s.PARTITION_NO, 0, '[]', 0, SYSTIMESTAMP)" in ensure
    # The guard is the caller's mark: the row advances only from mark.batch_no - 1; 0 rows
    # updated = another holder advanced the partition (ledger_conflict, nothing written).
    mark = osql.ledger_mark_sql("CRM")
    assert mark.startswith('UPDATE /* dps:ledger */ "CRM"."DPS_WRITE_LEDGER" l SET')
    assert mark.endswith("WHERE l.JOB_ID = :job AND l.PARTITION_NO = :part AND l.BATCH_NO = :prev")
    assert "l.ROWS_WRITTEN = :written" in mark
    assert "ROWS_WRITTEN" in osql.ledger_read_sql("CRM")
    assert osql.ledger_purge_sql("CRM") == (
        'DELETE /* dps:ledger */ FROM "CRM"."DPS_WRITE_LEDGER" WHERE JOB_ID = :job'
    )


def test_created_target_types() -> None:
    widened = NAME.model_copy(update={"length": 64})
    assert osql.oracle_ddl_type(widened, NAME) == "VARCHAR2(120 CHAR)"  # fits: keep the source type
    longer = NAME.model_copy(update={"length": 220})
    assert osql.oracle_ddl_type(longer, NAME) == "VARCHAR2(220 CHAR)"
    assert osql.oracle_ddl_type(NAME.model_copy(update={"length": 1500}), NAME) == "CLOB"
    as_text = CID.model_copy(update={"kind": "text", "length": 40})
    assert osql.oracle_ddl_type(as_text, CID) == "VARCHAR2(40 CHAR)"  # det_token on a NUMBER copy
    ddl = osql.create_table_sql(
        ObjectRef(owner="CRM_MASKED", name="CUSTOMERS"), [(CID, CID), (NAME, NAME)], ["CUSTOMER_ID"]
    )
    assert ddl.startswith(
        'CREATE TABLE "CRM_MASKED"."CUSTOMERS" ("CUSTOMER_ID" NUMBER(12) NOT NULL'
    )
    assert re.search(r'CONSTRAINT "DPS_PK_[0-9A-F]{8}" PRIMARY KEY \("CUSTOMER_ID"\)\)$', ddl)
    with pytest.raises(ValueError):
        osql.oracle_ddl_type(
            ColumnInfo(path="$.X", name="X", data_type="XMLTYPE", kind="other"), None
        )


def test_catalog_names_are_quoted_exactly() -> None:
    assert osql.qcol("FULL_NAME") == '"FULL_NAME"' and osql.qcol("firstName") == '"firstName"'
    with pytest.raises(ValueError):
        osql.qcol('bad"name')


def test_qualified_names_go_through_the_identifier_rule() -> None:
    assert osql.qualified(ObjectRef(owner="crm", name="customers")) == '"CRM"."CUSTOMERS"'
    with pytest.raises(ValueError):
        osql.qualified(ObjectRef(name="CUSTOMERS"))  # an Oracle object needs an owner


def test_pseudo_columns_are_not_table_data() -> None:
    assert ColumnInfo(path="$.A", name="A", data_type="NUMBER", kind="int").pseudo is False


def test_selection_sample_is_a_key_hash_filter() -> None:
    # SAMPLE … SEED is not stable across access paths (measured: COUNT(*) 81 rows, keyset pages
    # 72 of 200), so a selection's sample is a deterministic function of the key, as on PostgreSQL
    assert osql.sample_filter([CID], 40.0, 7) == 'ORA_HASH(t."CUSTOMER_ID", 999999, 7) < 400000'
    composite = osql.sample_filter([CID, TS9], 12.5, -1)
    assert composite == (
        'ORA_HASH(ORA_HASH(t."CUSTOMER_ID") * 4294967296 + ORA_HASH(t."EVENT_TS"), 999999, '
        "4294967295) < 125000"
    )
    assert osql.sample_filter([osql.rowid_column()], 100.0, 0) == (
        "ORA_HASH(t.ROWID, 999999, 0) < 1000000"
    )
    with pytest.raises(ValueError):
        osql.sample_filter([], 10.0, 0)
