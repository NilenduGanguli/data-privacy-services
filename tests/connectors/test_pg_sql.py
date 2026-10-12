"""PostgreSQL SQL building and bind coercion (offline)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from dps.jobs_models import ObjectRef, SampleSpec, Selection
from dps.layers.connectors.base import ColumnInfo, TableShape
from dps.layers.connectors.postgres import PG_KINDS, coerce_bind, select_sql

COLS = [
    ColumnInfo(path="$.a", name="a", data_type="bigint", kind="int", is_key=True),
    ColumnInfo(path="$.b", name="b", data_type="text", kind="text", is_key=True),
    ColumnInfo(path="$.c", name="c", data_type="text", kind="text"),
]
SHAPE = TableShape(
    object=ObjectRef(owner="s", name="t"), columns=COLS, key=["a", "b"], key_source="primary_key"
)


def test_keyset_sql_uses_row_values_and_continues_bind_numbers() -> None:
    sql, args = select_sql(
        SHAPE,
        Selection(),
        projection=COLS,
        body_sql='"c" = $1',
        body_args=["x"],
        lower=(1, "a"),
        upper=(9, "z"),
        after=(5, "m"),
        limit=100,
    )
    assert sql == (
        'SELECT t."a", t."b", t."c" FROM "s"."t" AS t WHERE ("c" = $1) AND (t."a", t."b") >= ($2, $3) '
        'AND (t."a", t."b") < ($4, $5) AND (t."a", t."b") > ($6, $7) ORDER BY t."a", t."b" LIMIT 100'
    )
    assert args == ["x", 1, "a", 9, "z", 5, "m"]


def test_single_column_keys_compare_scalars() -> None:
    shape = SHAPE.model_copy(update={"key": ["a"]})
    sql, args = select_sql(
        shape, Selection(), projection=COLS[:1], body_sql="", body_args=[], after=(7,), limit=5
    )
    assert sql == 'SELECT t."a" FROM "s"."t" AS t WHERE t."a" > $1 ORDER BY t."a" LIMIT 5'
    assert args == [7]


def test_query_wrapping_count_and_sample_filter() -> None:
    sel = Selection(query="SELECT a, b, c FROM s.t", sample=SampleSpec(percent=12.5, seed=7))
    sql, args = select_sql(
        SHAPE,
        sel,
        projection=COLS,
        body_sql='SELECT "a", "b", "c" FROM "s"."t"',
        body_args=[],
        count=True,
    )
    assert sql.startswith('SELECT count(*) FROM (SELECT "a", "b", "c" FROM "s"."t") AS q WHERE ')
    assert 'hashtextextended(concat_ws(\'|\', q."a"::text, q."b"::text), 7)' in sql
    assert "< 125000" in sql
    assert args == []


def test_tablesample_applies_to_tables_only() -> None:
    sql, _ = select_sql(
        SHAPE, Selection(), projection=COLS, body_sql="", body_args=[], order=False, tablesample=2.5
    )
    assert 'FROM "s"."t" AS t TABLESAMPLE SYSTEM (2.5000)' in sql and "ORDER BY" not in sql


def test_identifiers_are_validated_before_interpolation() -> None:
    from dps.layers.connectors.selection import SelectionError

    bad = TableShape(
        object=ObjectRef(owner="s", name="t"),
        columns=[ColumnInfo(path="$.x", name='x"; drop', data_type="int", kind="int")],
        key=['x"; drop'],
        key_source="primary_key",
    )
    with pytest.raises(SelectionError):
        select_sql(bad, Selection(), projection=bad.columns, body_sql="", body_args=[])


@pytest.mark.parametrize(
    "value,typ,expected",
    [
        ("2025-01-01", "date", date(2025, 1, 1)),
        ("2025-01-01T10:00:00", "timestamptz", datetime(2025, 1, 1, 10, tzinfo=UTC)),
        ("12.50", "numeric", Decimal("12.50")),
        (5, "numeric", Decimal(5)),
        ("42", "int8", 42),
        ("x", "text", "x"),
        (3, "text", "3"),
        ("true", "bool", True),
        (None, "date", None),
        (date(2025, 1, 1), "date", date(2025, 1, 1)),
    ],
)
def test_coerce_bind(value: object, typ: str, expected: object) -> None:
    assert coerce_bind(value, typ) == expected


def test_coerce_bind_more_types() -> None:
    uid = "6f9619ff-8b86-d011-b42d-00cf4fc964ff"
    assert coerce_bind(uid, "uuid") == UUID(uid)
    assert coerce_bind("0aff", "bytea") == b"\x0a\xff"
    assert coerce_bind("1.5", "float8") == 1.5
    assert coerce_bind("2025-01-01T10:00:00", "timestamp") == datetime(2025, 1, 1, 10)


def test_coerce_bind_errors_are_value_free() -> None:
    with pytest.raises(ValueError) as err:
        coerce_bind("not-a-date-123-45-6789", "date")
    assert "6789" not in str(err.value)
    with pytest.raises(ValueError) as err2:
        coerce_bind("123-45-6789", "int8")
    assert "6789" not in str(err2.value)


def test_kinds_cover_the_common_types() -> None:
    assert PG_KINDS["int8"] == "int" and PG_KINDS["bpchar"] == "char"
    assert PG_KINDS["jsonb"] == "json" and PG_KINDS["bytea"] == "binary"
    assert PG_KINDS["timestamptz"] == "timestamp" and PG_KINDS["uuid"] == "uuid"


def _self_signed_pem() -> str:
    from datetime import timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "dps-test-ca")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def test_the_ca_bundle_reference_resolves_in_the_connections_own_tenant(tmp_path) -> None:  # noqa: ANN001
    import ssl

    from dps.config import Settings
    from dps.connections import Credentials
    from dps.layers.connectors.base import ConnectorError
    from dps.layers.connectors.postgres import PostgresConnector
    from tests.connectors.factories import make_connection

    folder = tmp_path / "t1" / "dps" / "ca" / "bank"
    folder.mkdir(parents=True)
    (folder / "ca.pem").write_text(_self_signed_pem())
    settings = Settings(_env_file=None, secret_dir=str(tmp_path))
    params = {
        "host": "127.0.0.1",
        "port": 5432,
        "database": "bank",
        "options": {"sslmode": "verify-full", "sslrootcert_ref": "dps/ca/bank"},
    }
    own = make_connection("c_1", roles=["source"], params=params, tenant_id="t1")
    ctx = PostgresConnector(own, Credentials(), settings, action="test")._ssl()
    assert isinstance(ctx, ssl.SSLContext) and ctx.check_hostname
    other = make_connection("c_2", roles=["source"], params=params, tenant_id="t2")
    with pytest.raises(ConnectorError) as err:  # t2's folder has no such CA bundle
        PostgresConnector(other, Credentials(), settings, action="test")._ssl()
    assert err.value.code == "secret_unavailable"
