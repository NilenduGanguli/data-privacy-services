"""Pure Oracle rules for the DPS connector: the closed type table, SQL builders, the ROWID codec.

No driver I/O happens here, so every statement shape is unit-tested offline. The rules are measured,
not chosen (Wega orchestrator on Oracle 23ai Free with python-oracledb 26.0.0 thin):

* Reads never lock. No statement built here contains FOR UPDATE; FETCH FIRST appears only in reads,
  which is legal precisely because they do not lock (Wega L21).
* The select list is positional and real columns are unaliased; ORDER BY names the table-qualified
  column (t."K"). An alias equal to a column name would shadow it in ORDER BY and sort numbers as text
  (Wega T2-ORA-ID-EXACT).
* TIMESTAMP(7-9) crosses the driver as FF9 text both ways: the driver's datetime holds microseconds,
  and a key truncated to microseconds never pages past a burst of rows inside one microsecond.
* TIMESTAMP(0-6) binds are typed TIMESTAMP: the thin driver binds a bare datetime as DATE, which drops
  the fractional seconds, so a write loses its microseconds and a key bound compared at second
  precision re-reads the same rows on every page (measured: 10 rows, 120 reads).
* A key sample for partition boundaries keeps the first key of every run of ceil(total / n) keys in
  key order (quantiles of the selected set, as M2's PostgreSQL ``sample_keys``); ``ORDER BY key FETCH
  FIRST n`` would return the n lowest keys and cut every boundary from the bottom of the key space.
* CHAR binds are typed CHAR and blank-padded to the declared length; a VARCHAR2 bind compares without
  padding, so 'K17' never matches the stored 'K17         '.
* CLOB/NCLOB are fetched whole as str and never addressed by offset: CLOB offsets count UTF-16 code
  units and Python counts code points, so an emoji shifts every later offset by one.
* The write ledger's guard is the caller's mark (contract §0 amendment 2026-10-12): the partition's
  row is created at batch 0 first, then advanced only from ``mark.batch_no - 1`` by a guarded UPDATE
  whose row count is the fence (Oracle restarts an UPDATE whose WHERE columns another transaction
  changed while it waited for the row lock, so a zombie that waited re-reads and matches 0 rows).

Nothing here sees a value: statements carry binds only, and errors name a rule, never an input.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Final, Literal

from dps.layers.connectors.base import ColumnInfo, ColumnKind, ObjectRef, TableShape
from dps.layers.connectors.selection import quote_ident

TS9_FORMAT: Final = 'YYYY-MM-DD"T"HH24:MI:SS.FF9'
TSTZ_FORMAT: Final = 'YYYY-MM-DD"T"HH24:MI:SS.FF9TZH:TZM'
LEDGER_TABLE: Final = "DPS_WRITE_LEDGER"
IN_LIST_MAX: Final = 500  # Oracle allows 1000 expressions in an IN list
TEXT_VARCHAR_MAX_CHARS: Final = 1000  # created targets: wider text goes to CLOB (4000-byte limit)

Family = Literal[
    "number",
    "float",
    "varchar",
    "nvarchar",
    "char",
    "nchar",
    "date",
    "timestamp",
    "timestamp_tz",
    "clob",
    "nclob",
    "blob",
    "raw",
    "json",
    "unsupported",
]
InputType = Literal["char", "nchar", "nvarchar", "clob", "nclob", "blob", "timestamp"]
KeyOp = Literal[">", ">=", "<"]

_TS = re.compile(r"^TIMESTAMP(?:\((\d)\))?$")
_TSTZ = re.compile(r"^TIMESTAMP(?:\((\d)\))? WITH TIME ZONE$")
_SIMPLE_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SIMPLE: Final[dict[str, Family]] = {
    "NUMBER": "number",
    "FLOAT": "float",
    "BINARY_FLOAT": "float",
    "BINARY_DOUBLE": "float",
    "VARCHAR2": "varchar",
    "NVARCHAR2": "nvarchar",
    "CHAR": "char",
    "NCHAR": "nchar",
    "DATE": "date",
    "CLOB": "clob",
    "NCLOB": "nclob",
    "BLOB": "blob",
    "RAW": "raw",
    "JSON": "json",
}
_KIND: Final[dict[Family, ColumnKind]] = {
    "number": "decimal",
    "float": "float",
    "varchar": "text",
    "nvarchar": "text",
    "char": "char",
    "nchar": "char",
    "date": "timestamp",
    "timestamp": "timestamp",
    "timestamp_tz": "timestamp",
    "clob": "lob_text",
    "nclob": "lob_text",
    "blob": "lob_binary",
    "raw": "binary",
    "json": "json",
    "unsupported": "other",
}


@dataclass(frozen=True, slots=True)
class CatalogColumn:
    """One ALL_TAB_COLS row (visible, non-virtual, user-generated), as describe() reads it."""

    name: str
    data_type: str
    data_length: int
    char_length: int
    char_used: str | None
    data_precision: int | None
    data_scale: int | None
    nullable: bool
    has_default: bool
    is_identity: bool


@dataclass(frozen=True, slots=True)
class ReadPlan:
    """A read statement, the columns its select list yields in order, and typed binds."""

    sql: str
    columns: list[ColumnInfo]
    bind_types: dict[str, InputType]


def family_of(data_type: str) -> Family:
    """The closed family of a catalogue type; anything not listed is ``unsupported``."""
    if data_type in _SIMPLE:
        return _SIMPLE[data_type]
    if _TS.match(data_type):
        return "timestamp"
    if _TSTZ.match(data_type):
        return "timestamp_tz"
    return "unsupported"  # LONG, LONG RAW, BFILE, XMLTYPE, (U)ROWID columns, TSLTZ, VECTOR, objects


def column_path(name: str) -> str:
    """``$.NAME`` for plain identifiers, ``$['A$B']`` otherwise (``$``/``#`` are legal in Oracle)."""
    return f"$.{name}" if _SIMPLE_PATH.fullmatch(name) else f"$['{name}']"


def _render_type(c: CatalogColumn, fam: Family) -> str:
    if fam == "number":
        if c.data_precision is None:
            return "NUMBER(38)" if c.data_scale == 0 else "NUMBER"
        if not c.data_scale:
            return f"NUMBER({c.data_precision})"
        return f"NUMBER({c.data_precision},{c.data_scale})"
    if fam == "float" and c.data_type == "FLOAT":
        return f"FLOAT({c.data_precision or 126})"
    if fam in ("varchar", "char"):
        if c.char_used == "C":
            return f"{c.data_type}({c.char_length} CHAR)"
        return f"{c.data_type}({c.data_length})"
    if fam in ("nvarchar", "nchar"):
        return f"{c.data_type}({c.char_length})"
    if fam == "raw":
        return f"RAW({c.data_length})"
    return c.data_type


def column_info(c: CatalogColumn, *, is_key: bool) -> ColumnInfo:
    """Map one catalogue row to the contract's ColumnInfo (kind, length, precision, ts_precision)."""
    fam = family_of(c.data_type)
    kind: ColumnKind = "int" if fam == "number" and c.data_scale == 0 else _KIND[fam]
    ts_precision: int | None = None
    if fam == "date":
        ts_precision = 0
    elif fam in ("timestamp", "timestamp_tz"):
        m = _TS.match(c.data_type) or _TSTZ.match(c.data_type)
        ts_precision = int(m.group(1)) if m and m.group(1) else 6
    length: int | None = None
    if fam in ("varchar", "char"):
        length = c.char_length if c.char_used == "C" else c.data_length
    elif fam in ("nvarchar", "nchar"):
        length = c.char_length
    elif fam == "raw":
        length = c.data_length
    return ColumnInfo(
        path=column_path(c.name),
        name=c.name,
        data_type=_render_type(c, fam),
        kind=kind,
        length=length,
        precision=c.data_precision,
        scale=c.data_scale,
        ts_precision=ts_precision,
        nullable=c.nullable,
        is_key=is_key,
        has_default=c.has_default,
        is_identity=c.is_identity,
    )


def rowid_column() -> ColumnInfo:
    """The ROWID pseudo key of a key-less table: a packed integer, read-only, never written."""
    return ColumnInfo(
        path="$.ROWID",
        name="ROWID",
        data_type="ROWID",
        kind="int",
        nullable=False,
        is_key=True,
        pseudo=True,
    )


def qcol(name: str) -> str:
    """Quote a catalogue-derived column name exactly as the catalogue spells it.

    Names typed by a person go through ``selection.quote_ident`` (validated and upper-cased, the Wega
    rule). Names read from ALL_TAB_COLS are already exact, possibly mixed-case, and are quoted verbatim;
    Oracle forbids double quotes and NUL inside identifiers, so neither can appear.
    """
    if not name or len(name) > 128 or '"' in name or "\x00" in name:
        raise ValueError("not a valid catalogue identifier")
    return f'"{name}"'


def qualified(obj: ObjectRef) -> str:
    """``"OWNER"."NAME"`` through the validated, upper-casing identifier rule."""
    if not obj.owner:
        raise ValueError("an Oracle object needs an owner")
    return f"{quote_ident(obj.owner, 'oracle')}.{quote_ident(obj.name, 'oracle')}"


def is_tz(col: ColumnInfo) -> bool:
    """A zoned timestamp (TIMESTAMP WITH TIME ZONE): carried as text with its offset."""
    return col.kind == "timestamp" and "TIME ZONE" in col.data_type


def is_ts9(col: ColumnInfo) -> bool:
    """TIMESTAMP(7..9) without a zone: carried as FF9 text both ways."""
    return col.kind == "timestamp" and not is_tz(col) and (col.ts_precision or 0) > 6


def select_expr(col: ColumnInfo, alias: str = "t") -> str:
    """Positional, unaliased select expression of one column."""
    if col.pseudo:
        return f"ROWIDTOCHAR({alias}.ROWID)"
    q = f"{alias}.{qcol(col.name)}"
    if is_tz(col):
        return f"TO_CHAR({q}, '{TSTZ_FORMAT}')"
    if is_ts9(col):
        return f"TO_CHAR({q}, '{TS9_FORMAT}')"
    if col.data_type == "JSON":
        return f"JSON_SERIALIZE({q} RETURNING CLOB)"
    return q


def sort_expr(col: ColumnInfo, alias: str = "t") -> str:
    """Column side of a key predicate and of ORDER BY (index-matching, never an alias)."""
    if col.pseudo:
        return f"{alias}.ROWID"
    q = f"{alias}.{qcol(col.name)}"
    return f"SYS_EXTRACT_UTC({q})" if is_tz(col) else q


def value_expr(col: ColumnInfo, bind: str) -> str:
    """Value side of a write or predicate: the bind converted back to the column's type."""
    if col.pseudo:
        return f"CHARTOROWID(:{bind})"
    if is_tz(col):
        return f"TO_TIMESTAMP_TZ(:{bind}, '{TSTZ_FORMAT}')"
    if is_ts9(col):
        return f"TO_TIMESTAMP(:{bind}, '{TS9_FORMAT}')"
    if col.data_type == "JSON":
        return f"JSON(:{bind})"
    return f":{bind}"


def key_value_expr(col: ColumnInfo, bind: str) -> str:
    """Value side of a key predicate; zoned timestamps compare in UTC like ``sort_expr``."""
    return f"SYS_EXTRACT_UTC({value_expr(col, bind)})" if is_tz(col) else value_expr(col, bind)


def input_type(col: ColumnInfo) -> InputType | None:
    """The bind type a value of this column needs (``None`` = the driver's default is right)."""
    dt = col.data_type
    if dt.startswith("CHAR("):
        return "char"
    if dt.startswith("NCHAR("):
        return "nchar"
    if dt.startswith("NVARCHAR2("):
        return "nvarchar"
    if dt in ("CLOB", "JSON"):
        return "clob"
    if dt == "NCLOB":
        return "nclob"
    if dt == "BLOB":
        return "blob"
    if _TS.match(dt) and not is_ts9(col):
        return "timestamp"  # TIMESTAMP(0-6): a bare datetime binds as DATE and loses its fraction
    return None


def pad_char(value: object, length: int | None) -> object:
    """Blank-pad a CHAR value to its declared length (a rstripped canonical text binds back right)."""
    if isinstance(value, str) and length and len(value) < length:
        return value.ljust(length)
    return value


def tuple_predicate(cols: list[str], values: list[str], op: KeyOp) -> str:
    """Lexicographic ``(c1..cn) op (v1..vn)`` in the expanded form Oracle can range-scan.

    ``(a, b) > (x, y)`` becomes ``(a >= x AND (a > x OR (a = x AND b > y)))``: the leading conjunct is
    an index range on the first key column (the Wega keyset form); the rest decides ties.
    """
    if not cols or len(cols) != len(values):
        raise ValueError("a key predicate needs one value per key column")
    first = ">" if op in (">", ">=") else "<"

    def rec(i: int) -> str:
        if i == len(cols) - 1:
            return f"{cols[i]} {op} {values[i]}"
        return f"({cols[i]} {first} {values[i]} OR ({cols[i]} = {values[i]} AND {rec(i + 1)}))"

    body = rec(0)
    if len(cols) == 1:
        return body
    lead = ">=" if first == ">" else "<="
    return f"({cols[0]} {lead} {values[0]} AND {body})"


def key_columns(shape: TableShape) -> list[ColumnInfo]:
    """The key columns of ``shape`` in key order."""
    return shape.key_columns()


def _source(shape: TableShape, query: str | None, sample: tuple[float, int] | None) -> str:
    if query is not None:
        return f"({query}) t"
    if sample is not None and 0 < sample[0] < 100:
        return f"{qualified(shape.object)} SAMPLE ({sample[0]:.6f}) SEED ({int(sample[1])}) t"
    return f"{qualified(shape.object)} t"


_HASH_SPAN: Final = 4_294_967_296  # ORA_HASH returns 0 .. 2**32 - 1; its seed takes the same range
_HASH_BUCKETS: Final = 1_000_000


def sample_filter(keys: list[ColumnInfo], percent: float, seed: int, alias: str = "t") -> str:
    """A selection's ``sample`` as a predicate on a hash of the key (no binds, integers only).

    ``SAMPLE (p) SEED (s)`` is not stable across access paths: measured on 23ai, ``COUNT(*)``
    with ``SAMPLE (40) SEED (7)`` saw 81 of 200 rows while keyset pages of the same clause saw 72,
    so partitions and the count would not split one sample. A hash of the key is a function of
    the row alone (keys are never rewritten in place), like M2's PostgreSQL filter. Composite keys
    fold the per-column hashes; the result keeps ``percent`` of the 10**6 buckets.

    Raises:
        ValueError: no key columns.
    """
    if not keys:
        raise ValueError("a sample filter needs the key columns")
    exprs = [sort_expr(k, alias) for k in keys]
    acc = exprs[0]
    for i, expr in enumerate(exprs[1:]):
        head = f"ORA_HASH({acc})" if i == 0 else acc
        acc = f"ORA_HASH({head} * {_HASH_SPAN} + ORA_HASH({expr}))"
    if len(exprs) > 1:
        acc = acc[len("ORA_HASH(") : -1]  # the outer hash below takes the bucket count and seed
    threshold = int(round(percent * _HASH_BUCKETS / 100))
    return f"ORA_HASH({acc}, {_HASH_BUCKETS - 1}, {seed % _HASH_SPAN}) < {threshold}"


_KEY_BOUNDS: Final[tuple[tuple[str, KeyOp], ...]] = (("lo", ">="), ("hi", "<"), ("af", ">"))


def read_sql(
    shape: TableShape,
    *,
    columns: list[ColumnInfo],
    where: str | None,
    query: str | None,
    sample: tuple[float, int] | None,
    lower: bool,
    upper: bool,
    after: bool,
) -> ReadPlan:
    """One keyset page: ``key >= lower AND key < upper AND key > after ORDER BY key FETCH FIRST``.

    Binds: ``:lo<i>``, ``:hi<i>``, ``:af<i>`` per key column (only the bounds in use), ``:batch``,
    and the selection's own ``:name`` binds inside ``where`` / ``query``.
    """
    keys = key_columns(shape)
    sorts = [sort_expr(k) for k in keys]
    conds = [f"({where})"] if where else []
    bind_types: dict[str, InputType] = {}
    used = {"lo": lower, "hi": upper, "af": after}
    for prefix, op in _KEY_BOUNDS:
        if not used[prefix]:
            continue
        values = [key_value_expr(k, f"{prefix}{i}") for i, k in enumerate(keys)]
        conds.append(tuple_predicate(sorts, values, op))
        for i, k in enumerate(keys):
            if (t := input_type(k)) is not None:
                bind_types[f"{prefix}{i}"] = t
    select = ", ".join(select_expr(c) for c in columns)
    where_sql = f" WHERE {' AND '.join(conds)}" if conds else ""
    sql = (
        f"SELECT /* dps:read */ {select} FROM {_source(shape, query, sample)}{where_sql}"
        f" ORDER BY {', '.join(sorts)} FETCH FIRST :batch ROWS ONLY"
    )
    return ReadPlan(sql=sql, columns=list(columns), bind_types=bind_types)


def count_sql(
    shape: TableShape, *, where: str | None, query: str | None, sample: tuple[float, int] | None
) -> str:
    """``COUNT(*)`` of the selection (no lock, no order)."""
    where_sql = f" WHERE ({where})" if where else ""
    return f"SELECT /* dps:count */ COUNT(*) FROM {_source(shape, query, sample)}{where_sql}"


def sample_sql(
    shape: TableShape,
    *,
    columns: list[ColumnInfo],
    where: str | None,
    query: str | None,
    percent: float | None,
    keys_only: bool = False,
    upper: bool = False,
) -> ReadPlan:
    """A random sample for discovery (``keys_only`` for partition boundaries); no locks.

    Bind ``:n`` caps the rows. A row sample is unordered (``FETCH FIRST :n``). ``keys_only`` returns
    the key quantiles of the selected set in key order: ``ROW_NUMBER() OVER (ORDER BY key)`` ranks
    in the database's order and the first key of every run of ``ceil(total / n)`` keys is kept, at
    most ``n`` keys spread over the whole set. ``upper`` bounds that set below ``:hi<i>`` (the global
    read bound, typed like ``read_sql``'s), so the quantiles are those of the bounded set.
    """
    cols = key_columns(shape) if keys_only else columns
    if query is None and percent is not None and 0 < percent < 100:
        src = f"{qualified(shape.object)} SAMPLE ({percent:.6f}) t"  # unseeded: a fresh sample
    else:
        src = _source(shape, query, None)  # small table or a query
    conds = [f"({where})"] if where else []
    if not keys_only:
        where_sql = f" WHERE {conds[0]}" if conds else ""
        sql = (
            f"SELECT /* dps:sample */ {', '.join(select_expr(c) for c in cols)} FROM {src}"
            f"{where_sql} FETCH FIRST :n ROWS ONLY"
        )
        return ReadPlan(sql=sql, columns=list(cols), bind_types={})
    sorts = [sort_expr(k) for k in cols]
    bind_types: dict[str, InputType] = {}
    if upper:
        values = [key_value_expr(k, f"hi{i}") for i, k in enumerate(cols)]
        conds.append(tuple_predicate(sorts, values, "<"))
        for i, k in enumerate(cols):
            if (t := input_type(k)) is not None:
                bind_types[f"hi{i}"] = t
    where_sql = f" WHERE {' AND '.join(conds)}" if conds else ""
    # DPS_* aliases name the inline view's columns only; the window's ORDER BY names the
    # table-qualified columns (an analytic ORDER BY never sees select-list aliases).
    ranked = ", ".join(f"{select_expr(k)} DPS_K{i}" for i, k in enumerate(cols))
    sql = (
        f"SELECT /* dps:sample */ {', '.join(f's.DPS_K{i}' for i in range(len(cols)))} FROM "
        f"(SELECT {ranked}, ROW_NUMBER() OVER (ORDER BY {', '.join(sorts)}) DPS_RN, "
        f"COUNT(*) OVER () DPS_N FROM {src}{where_sql}) s "
        "WHERE MOD(s.DPS_RN - 1, CEIL(s.DPS_N / GREATEST(:n, 1))) = 0 ORDER BY s.DPS_RN"
    )
    return ReadPlan(sql=sql, columns=list(cols), bind_types=bind_types)


def lookup_sql(shape: TableShape, join: ColumnInfo, columns: list[ColumnInfo], n: int) -> ReadPlan:
    """Rows whose ``join`` value is one of ``:v0 … :v<n-1>`` (consistency check; no locks).

    The predicate compares the index-matching column side with typed binds (CHAR binds are
    blank-padded by the caller), at most :data:`IN_LIST_MAX` values per statement.

    Raises:
        ValueError: ``n`` is outside ``1 … IN_LIST_MAX``.
    """
    if not 1 <= n <= IN_LIST_MAX:
        raise ValueError("a lookup binds 1 to IN_LIST_MAX values")
    items = ", ".join(key_value_expr(join, f"v{i}") for i in range(n))
    sql = (
        f"SELECT /* dps:lookup */ {', '.join(select_expr(c) for c in columns)} "  # noqa: S608
        f"FROM {qualified(shape.object)} t WHERE {sort_expr(join)} IN ({items})"
    )
    t = input_type(join)
    return ReadPlan(
        sql=sql, columns=list(columns), bind_types={f"v{i}": t for i in range(n)} if t else {}
    )


def merge_sql(target: ObjectRef, cols: list[ColumnInfo], keys: list[str]) -> str:
    """Upsert one row per bind set (positional ``:v0..``) on the key — idempotent table loads."""
    src = ", ".join(f"{value_expr(c, f'v{i}')} AS {qcol(c.name)}" for i, c in enumerate(cols))
    on = " AND ".join(f"d.{qcol(k)} = s.{qcol(k)}" for k in keys)
    non_key = [c for c in cols if c.name not in keys]
    update = (
        " WHEN MATCHED THEN UPDATE SET "
        + ", ".join(f"d.{qcol(c.name)} = s.{qcol(c.name)}" for c in non_key)
        if non_key
        else ""
    )
    names = ", ".join(qcol(c.name) for c in cols)
    vals = ", ".join(f"s.{qcol(c.name)}" for c in cols)
    return (
        f"MERGE /* dps:merge */ INTO {qualified(target)} d USING (SELECT {src} FROM dual) s "
        f"ON ({on}){update} WHEN NOT MATCHED THEN INSERT ({names}) VALUES ({vals})"
    )


def update_sql(target: ObjectRef, set_cols: list[ColumnInfo], key_cols: list[ColumnInfo]) -> str:
    """In-place update by key; binds ``:v0..`` are the SET values, then the key values."""
    sets = ", ".join(f"d.{qcol(c.name)} = {value_expr(c, f'v{i}')}" for i, c in enumerate(set_cols))
    n = len(set_cols)
    where = " AND ".join(
        f"d.{qcol(k.name)} = {value_expr(k, f'v{n + i}')}" for i, k in enumerate(key_cols)
    )
    return f"UPDATE /* dps:update */ {qualified(target)} d SET {sets} WHERE {where}"


def insert_sql(
    target: ObjectRef, cols: list[ColumnInfo], *, sequence: tuple[str, str] | None = None
) -> str:
    """Append rows; ``sequence = (key column, quoted sequence)`` fills that key from NEXTVAL."""
    names: list[str] = []
    vals: list[str] = []
    i = 0
    for c in cols:
        names.append(qcol(c.name))
        if sequence is not None and c.name == sequence[0]:
            vals.append(f"{sequence[1]}.NEXTVAL")
        else:
            vals.append(value_expr(c, f"v{i}"))
            i += 1
    return (
        f"INSERT /* dps:insert */ INTO {qualified(target)} ({', '.join(names)}) "
        f"VALUES ({', '.join(vals)})"
    )


def collision_sql(target: ObjectRef, keys: list[ColumnInfo], n: int) -> str:
    """Count how many of ``n`` candidate keys already exist (append offset/token pre-flight).

    Binds ``:c<j>`` (single key) or ``:c<j>_<i>`` (composite); callers keep ``n <= IN_LIST_MAX``.
    """
    if len(keys) == 1:
        k = keys[0]
        items = ", ".join(value_expr(k, f"c{j}") for j in range(n))
        cond = f"d.{qcol(k.name)} IN ({items})"
    else:
        cols = ", ".join(f"d.{qcol(k.name)}" for k in keys)
        tuples = ", ".join(
            "(" + ", ".join(value_expr(k, f"c{j}_{i}") for i, k in enumerate(keys)) + ")"
            for j in range(n)
        )
        cond = f"({cols}) IN ({tuples})"
    return f"SELECT /* dps:collisions */ COUNT(*) FROM {qualified(target)} d WHERE {cond}"


# --- write ledger (contract §4.2; one row per (job, partition) in the target owner's schema) -------


def _ledger(owner: str) -> str:
    return f'{quote_ident(owner, "oracle")}."{LEDGER_TABLE}"'


def ledger_ddl(owner: str) -> str:
    """Create the write ledger once; ORA-00955 (exists) is ignored — Oracle 19c has no IF NOT EXISTS.

    ``ROWS_WRITTEN`` holds ``LedgerMark.rows`` (cumulative rows the partition wrote).
    """
    ddl = (
        f"CREATE TABLE {_ledger(owner)} (JOB_ID VARCHAR2(40) NOT NULL, "
        "PARTITION_NO NUMBER(10) NOT NULL, BATCH_NO NUMBER(10) NOT NULL, LAST_KEY CLOB NOT NULL, "
        "ROWS_WRITTEN NUMBER(19) DEFAULT 0 NOT NULL, "
        "UPDATED_AT TIMESTAMP(6) DEFAULT SYSTIMESTAMP NOT NULL, "
        f'CONSTRAINT "{LEDGER_TABLE}_PK" PRIMARY KEY (JOB_ID, PARTITION_NO))'
    )
    escaped = ddl.replace("'", "''")
    return (
        "BEGIN EXECUTE IMMEDIATE '" + escaped + "'; "
        "EXCEPTION WHEN OTHERS THEN IF SQLCODE != -955 THEN RAISE; END IF; END;"
    )


def ledger_ensure_sql(owner: str) -> str:
    """Create the partition's ledger row at batch 0 when it is missing (binds ``:job``, ``:part``).

    Run first in a ledgered batch's transaction. A concurrent first batch of another holder waits
    on this uncommitted insert (a unique-index wait) instead of finding no row to lock; when that
    holder commits, this statement fails with ORA-00001, which the writer reports as
    ``ledger_conflict`` (the other holder applied the partition's first batch).
    """
    return (
        f"MERGE /* dps:ledger */ INTO {_ledger(owner)} l "
        "USING (SELECT :job AS JOB_ID, :part AS PARTITION_NO FROM dual) s "
        "ON (l.JOB_ID = s.JOB_ID AND l.PARTITION_NO = s.PARTITION_NO) "
        "WHEN NOT MATCHED THEN INSERT (JOB_ID, PARTITION_NO, BATCH_NO, LAST_KEY, ROWS_WRITTEN, "
        "UPDATED_AT) VALUES (s.JOB_ID, s.PARTITION_NO, 0, '[]', 0, SYSTIMESTAMP)"
    )


def ledger_mark_sql(owner: str) -> str:
    """Advance ``(job, partition)`` to the caller's mark — only from ``mark.batch_no - 1``.

    Binds ``:job``, ``:part``, ``:batch`` (the mark), ``:prev`` (``batch - 1``), ``:last_key`` (the
    encoded key as JSON text) and ``:written`` (``LedgerMark.rows``). Run after
    :func:`ledger_ensure_sql`, before the rows, in the batch's transaction: it takes the row lock
    (serialising a zombie writer that lost its lease) and its row count is the in-target fence —
    0 means another holder advanced (or skipped past) this batch, so the caller rolls back with
    ``ledger_conflict`` and writes nothing.
    """
    return (
        f"UPDATE /* dps:ledger */ {_ledger(owner)} l SET l.BATCH_NO = :batch, "
        "l.LAST_KEY = :last_key, l.ROWS_WRITTEN = :written, l.UPDATED_AT = SYSTIMESTAMP "
        "WHERE l.JOB_ID = :job AND l.PARTITION_NO = :part AND l.BATCH_NO = :prev"
    )


def ledger_read_sql(owner: str) -> str:
    """The partition's mark (binds ``:job``, ``:part``); a ``BATCH_NO = 0`` row means no mark."""
    return (
        f"SELECT /* dps:ledger */ BATCH_NO, LAST_KEY, ROWS_WRITTEN FROM {_ledger(owner)} "
        "WHERE JOB_ID = :job AND PARTITION_NO = :part"
    )


def ledger_purge_sql(owner: str) -> str:
    """Finalize: delete the job's ledger rows (bind ``:job``)."""
    return f"DELETE /* dps:ledger */ FROM {_ledger(owner)} WHERE JOB_ID = :job"


# --- created targets -------------------------------------------------------------------------------


def oracle_ddl_type(target: ColumnInfo, source: ColumnInfo | None) -> str:
    """The column type of a created target: the source type when the method output still fits it.

    Raises:
        ValueError: ``column_unsupported`` — the column's kind has no Oracle type here.
    """
    if (
        source is not None
        and family_of(source.data_type.split("(")[0]) != "unsupported"
        and target.kind == source.kind
        and (target.length or 0) <= (source.length or 0)
    ):
        return source.data_type
    match target.kind:
        case "int":
            return f"NUMBER({target.precision})" if target.precision else "NUMBER(38)"
        case "decimal":
            if target.precision:
                return f"NUMBER({target.precision},{target.scale or 0})"
            return "NUMBER"
        case "float":
            return "BINARY_DOUBLE"
        case "text" | "char":
            n = target.length or 4000
            return f"VARCHAR2({n} CHAR)" if n <= TEXT_VARCHAR_MAX_CHARS else "CLOB"
        case "timestamp":
            p = 6 if target.ts_precision is None else target.ts_precision
            return "DATE" if p == 0 else f"TIMESTAMP({p})"
        case "date":
            return "DATE"
        case "bool":
            return "NUMBER(1)"
        case "binary" | "uuid":
            return f"RAW({target.length or 2000})"
        case "lob_text" | "json":
            return "CLOB"
        case "lob_binary":
            return "BLOB"
    raise ValueError("column_unsupported")


def create_table_sql(
    target: ObjectRef, cols: list[tuple[ColumnInfo, ColumnInfo | None]], keys: list[str]
) -> str:
    """CREATE TABLE for a missing ``table`` target: types per ``oracle_ddl_type``, PK on the key."""
    parts = []
    for tgt, src in cols:
        null = " NOT NULL" if tgt.name in keys else ""
        parts.append(f"{qcol(tgt.name)} {oracle_ddl_type(tgt, src)}{null}")
    if keys:
        tag = hashlib.sha256(qualified(target).encode()).hexdigest()[:8].upper()
        parts.append(f'CONSTRAINT "DPS_PK_{tag}" PRIMARY KEY ({", ".join(qcol(k) for k in keys)})')
    return f"CREATE TABLE {qualified(target)} ({', '.join(parts)})"


# --- ROWID codec ----------------------------------------------------------------------------------
_B64: Final = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_IDX: Final = {ch: i for i, ch in enumerate(_B64)}
_ROW_BITS: Final = 18  # 3 base64 digits
_BLOCK_BITS: Final = 36  # 6 base64 digits
_FILE_BITS: Final = 18  # 3 base64 digits


def _dec(text: str) -> int:
    n = 0
    for ch in text:
        n = n * 64 + _IDX[ch]
    return n


def _enc(n: int, width: int) -> str:
    out = []
    for _ in range(width):
        n, r = divmod(n, 64)
        out.append(_B64[r])
    if n:
        raise ValueError("component out of range")
    return "".join(reversed(out))


def rowid_to_key(rowid: str) -> int:
    """Pack an extended ROWID (OOOOOOFFFBBBBBBRRR) into an int that sorts like Oracle's ROWID order.

    Oracle orders ROWIDs by (data object, relative file, block, row); the base64 text does not sort
    that way in Python (its alphabet is not ASCII-ordered), so partition boundaries use this integer.

    Raises:
        ValueError: not an 18-character extended (physical) ROWID — e.g. an IOT's logical ROWID.
    """
    if len(rowid) != 18 or any(ch not in _IDX for ch in rowid):
        raise ValueError("not an extended rowid")
    obj, fno, blk, row = _dec(rowid[0:6]), _dec(rowid[6:9]), _dec(rowid[9:15]), _dec(rowid[15:18])
    return (((obj << _FILE_BITS | fno) << _BLOCK_BITS | blk) << _ROW_BITS) | row


def key_to_rowid(key: int) -> str:
    """Inverse of :func:`rowid_to_key`."""
    row = key & (2**_ROW_BITS - 1)
    key >>= _ROW_BITS
    blk = key & (2**_BLOCK_BITS - 1)
    key >>= _BLOCK_BITS
    fno = key & (2**_FILE_BITS - 1)
    obj = key >> _FILE_BITS
    return _enc(obj, 6) + _enc(fno, 3) + _enc(blk, 6) + _enc(row, 3)
