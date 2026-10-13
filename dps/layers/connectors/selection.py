"""Selection ("control statement") validation and compilation with sqlglot (contract §5.2).

How text becomes SQL. A ``where`` is parsed as ``SELECT * FROM __dps_t WHERE <where>`` (``*``
rather than the contract's ``1``, so the wrapper adds no literal of its own) and must be one
boolean expression (no trailing ``ORDER BY``/``LIMIT``); a ``query`` must be exactly one
``SELECT``. The first violation is refused with a stable :class:`SelectionError` code. Denied
functions are refused as calls and in PostgreSQL attribute notation (``t.f`` and ``(expr).f``
call ``f``), wherever they appear in the tree. Comments
are stripped. Unquoted identifiers are folded (lower for PostgreSQL, upper for Oracle) and every
identifier is then quoted. Every literal value becomes a named bind (``:l1``, ``:l2``, …): a typed
literal (``DATE '…'``) becomes ``CAST(CAST(:l1 AS TEXT) AS DATE)`` so the driver binds text, a
PostgreSQL interval ``CAST(CAST(:l2 AS TEXT) AS INTERVAL)``, an Oracle interval
``NUMTODSINTERVAL(:l2, 'DAY')``. Structural literals stay in the text: ``ORDER BY``/``GROUP BY``
positions, type parameters (``VARCHAR(10)``), date/time format strings and interval units.
Rendering goes through sentinel columns ``__dps_bind_<name>`` that are swapped for ``$n``
(PostgreSQL execution) or ``:name`` (Oracle execution and value-free templates).

The API stores the **parameterised** selection (literal values in ``params``, sealed into
``spec_enc``) and only templates in ``dps_job.spec``; :func:`compile_where` and
:func:`compile_query` refuse a selection that still holds a literal value
(``selection_not_parameterized``). Error messages name the rule, never the SQL text.

Rejection codes: ``selection_parse_error``, ``selection_multiple_statements``,
``selection_not_select``, ``selection_dml``, ``selection_into``, ``selection_for_update``,
``selection_function_denied``, ``selection_identifier_invalid``, ``selection_positional_param``,
``selection_param_missing``, ``selection_unknown_column``, ``selection_query_missing_key``,
``selection_not_parameterized``, ``selection_literal_unsupported``, ``selection_operator_denied``,
``selection_engine_mismatch``.
"""

from __future__ import annotations

import re
from typing import Any, cast

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.tokens import TokenType

from dps.jobs_models import SampleSpec, Selection  # noqa: F401 - re-exported (contract §5.2)
from dps.layers.connectors.base import ColumnInfo, Engine, JsonScalar, TableShape

ORACLE_IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,127}$")
PG_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")
MONGO_PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
MONGO_OPERATORS = frozenset(
    {
        "$eq",
        "$ne",
        "$gt",
        "$gte",
        "$lt",
        "$lte",
        "$in",
        "$nin",
        "$and",
        "$or",
        "$nor",
        "$not",
        "$exists",
        "$type",
        "$size",
        "$all",
        "$elemMatch",
        "$regex",
    }
)
#: Function-name prefixes refused anywhere in a selection: the contract's deny-list, M2's
#: additions, and functions that run SQL from a string or signal other sessions (they would
#: escape this validation or the read-only transaction's intent). Matched on the lower-cased
#: bare name, so schema-qualified, quoted and upper-case spellings are refused alike. Calls are
#: matched against this list; attribute notation against :data:`DENIED_ATTRIBUTE_PREFIXES`.
DENIED_FUNCTION_PREFIXES: tuple[str, ...] = (
    "pg_sleep",
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "pg_stat_file",
    "lo_",
    "dblink",
    "dbms_",
    "utl_",
    "xmltype",
    "httpuritype",
    "set_config",
    "pg_terminate_backend",
    "pg_cancel_backend",
    "pg_advisory",
    "pg_try_advisory",
    "pg_reload_conf",
    "pg_rotate_logfile",
    "pg_promote",
    "pg_notify",
    "pg_logical_emit_message",
    "pg_switch_wal",
    "pg_create_restore_point",
    "pg_file_",
    "pg_stat_reset",
    # Functions that execute a SQL string: a bound literal would run any statement, including
    # the denied functions above, inside the connector's transaction.
    "query_to_xml",
    "cursor_to_xml",
    "ts_stat",  # core PostgreSQL ts_stat(sqlquery text [, weights text])
    "ts_rewrite",  # core PostgreSQL ts_rewrite(query tsquery, select text)
    "crosstab",  # tablefunc crosstab, crosstab2/3/4 and custom crosstab_* wrappers
    "connectby",  # tablefunc connectby(relname, …) builds and runs a query from text
    "xpath_table",  # xml2 xpath_table(key, doc, relation, xpaths, criteria) runs a built SELECT
    "pg_background",  # pg_background_launch(sql): a separate session, not read-only
)
_DENY_SCHEMAS = frozenset(
    {"sys", "dbms_lock", "dbms_pipe", "dbms_scheduler", "utl_http", "utl_file"}
)
#: The large-object functions of PostgreSQL 16 (``pg_proc``) and of the ``lo`` extension; prefix
#: matched, so ``lo_lseek64``, ``lo_tell64`` and ``lo_truncate64`` are included.
LARGE_OBJECT_FUNCTIONS: tuple[str, ...] = (
    "lo_close",
    "lo_creat",
    "lo_create",
    "lo_export",
    "lo_from_bytea",
    "lo_get",
    "lo_import",
    "lo_lseek",
    "lo_manage",
    "lo_open",
    "lo_put",
    "lo_tell",
    "lo_truncate",
    "lo_unlink",
    "loread",
    "lowrite",
)
#: Names refused where PostgreSQL reads a name as a call without call syntax (attribute notation:
#: ``t.f`` and ``(expr).f`` mean ``f(t)`` and ``f(expr)`` when ``f`` is not a column of ``t``).
#: The call deny-list, with the contract's ``lo_*`` narrowed to the real large-object functions
#: so that a qualified column such as ``a.lo_limit`` is not refused; calls keep ``lo_*``.
DENIED_ATTRIBUTE_PREFIXES: tuple[str, ...] = (
    tuple(p for p in DENIED_FUNCTION_PREFIXES if p != "lo_") + LARGE_OBJECT_FUNCTIONS
)
_PACKAGE_PREFIXES = ("dbms_", "utl_")
_BIND_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,30}$")
_RESERVED_PREFIX = "__dps_"
_WRAPPER = "__dps_t"
_SENTINEL = "__dps_bind_"
_SENTINEL_RE = re.compile(r'"?__dps_bind_([A-Za-z0-9_]+)"?', re.IGNORECASE)
_DIALECT: dict[str, str] = {"postgres": "postgres", "oracle": "oracle"}
_TEXT_TYPE: dict[str, str] = {"postgres": "text", "oracle": "varchar(4000)"}
_WHERE_ARGS = frozenset({"expressions", "from", "from_", "where"})
_ORACLE_DS_UNITS = frozenset({"DAY", "HOUR", "MINUTE", "SECOND"})
_ORACLE_YM_UNITS = frozenset({"YEAR", "MONTH"})
_INTERVAL_FUNCS = frozenset({"numtodsinterval", "numtoyminterval"})


def _node_types(*names: str) -> tuple[type[exp.Expression], ...]:
    """The sqlglot node classes among ``names`` (class names drift between sqlglot versions)."""
    found = [getattr(exp, n, None) for n in names]
    return tuple(t for t in found if isinstance(t, type))


_DML = _node_types(
    "Insert",
    "Update",
    "Delete",
    "Merge",
    "Create",
    "Drop",
    "Alter",
    "AlterTable",
    "TruncateTable",
    "Command",
    "Copy",
    "Set",
    "Use",
    "Transaction",
    "Commit",
    "Rollback",
    "Grant",
    "Revoke",
    "LoadData",
    "Pragma",
    "Analyze",
    "Describe",
)
_INTO = _node_types("Into")
_LOCK = _node_types("Lock")
#: String literals whose text is the value as written (bound like plain strings).
_PLAIN_STRINGS = _node_types("RawString", "National")
#: String forms whose text is not the value (escapes, bits, hex, unicode escapes): refused.
_UNSUPPORTED_STRINGS = _node_types("ByteString", "BitString", "HexString", "UnicodeString")


class SelectionError(ValueError):
    """A selection that DPS will not run; ``code`` is stable, the message never quotes the SQL."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


def quote_ident(name: str, engine: Engine) -> str:
    """Validate and quote one identifier (Oracle upper-cases, the Wega rule; PG keeps case)."""
    if engine == "oracle":
        if not ORACLE_IDENT.fullmatch(name):
            raise SelectionError("selection_identifier_invalid", "not a valid Oracle identifier")
        return f'"{name.upper()}"'
    if engine == "postgres":
        if not PG_IDENT.fullmatch(name):
            raise SelectionError(
                "selection_identifier_invalid", "not a valid PostgreSQL identifier"
            )
        return f'"{name}"'
    if not MONGO_PATH.fullmatch(name):
        raise SelectionError("selection_identifier_invalid", "not a valid MongoDB field path")
    return name


def _walk(node: exp.Expression) -> list[exp.Expression]:
    out: list[exp.Expression] = []
    for item in node.walk():
        # older sqlglot yields (node, parent, key) tuples; sqlglot 30 types nodes as ``Expr``
        out.append(cast(exp.Expression, item[0] if isinstance(item, tuple) else item))
    return out


def _is_value_literal_type(node: exp.Expression) -> bool:
    return isinstance(node, exp.Literal) or bool(
        _PLAIN_STRINGS and isinstance(node, _PLAIN_STRINGS)
    )


def _structural(node: exp.Expression) -> bool:
    """A literal that is SQL structure, not a value (kept in the text, never bound)."""
    parent = node.parent
    if isinstance(parent, exp.Ordered | exp.Group):  # ORDER BY 1 / GROUP BY 1 positions
        return True
    if node.find_ancestor(exp.DataType) is not None:  # VARCHAR(10), NUMERIC(10, 2)
        return True
    if node.arg_key == "format":  # TO_DATE(:x, 'YYYY-MM-DD'), TO_CHAR(col, 'YYYY')
        return True
    return (
        isinstance(parent, exp.Anonymous)
        and parent.name.lower() in _INTERVAL_FUNCS
        and node.index == 1
    )


def _unbound_literals(stmt: exp.Expression) -> list[exp.Expression]:
    return [n for n in _walk(stmt) if _is_value_literal_type(n) and not _structural(n)]


def _parse(text: str, engine: Engine, *, wrap_where: bool) -> exp.Select:
    if engine not in _DIALECT:
        raise SelectionError(
            "selection_engine_mismatch", "SQL selections apply to SQL engines only"
        )
    read = _DIALECT[engine]
    # SELECT * (not the contract's "SELECT 1"): the wrapper must contain no literal of its own
    sql = f"SELECT * FROM {_WRAPPER} WHERE {text}" if wrap_where else text
    try:
        tokens = sqlglot.tokenize(sql, read=read)
    except (SqlglotError, RecursionError):
        raise SelectionError(
            "selection_parse_error", "the statement could not be tokenised"
        ) from None
    if any(t.token_type == TokenType.SEMICOLON for t in tokens):
        raise SelectionError(
            "selection_multiple_statements", "only one statement is allowed; remove ';'"
        )
    try:
        parsed = [s for s in sqlglot.parse(sql, read=read) if s is not None]
    except (SqlglotError, RecursionError):
        raise SelectionError("selection_parse_error", "the statement could not be parsed") from None
    if len(parsed) != 1:
        raise SelectionError("selection_multiple_statements", "only one statement is allowed")
    stmt = parsed[0]
    if not isinstance(stmt, exp.Select):
        raise SelectionError("selection_not_select", "the statement must be a single SELECT")
    if wrap_where and any(v for k, v in stmt.args.items() if k not in _WHERE_ARGS):
        raise SelectionError(
            "selection_parse_error",
            "a where clause must be one boolean expression (no ORDER BY, LIMIT, GROUP BY…)",
        )
    for node in _walk(stmt):
        node.comments = None  # comments can quote values; they never reach a template
        if _DML and isinstance(node, _DML):  # e.g. a data-modifying CTE under the SELECT
            raise SelectionError(
                "selection_dml", "only SELECT is allowed (no DML, DDL or commands)"
            )
    return stmt


def _identifier_text(node: exp.Identifier) -> str:
    return node.this if isinstance(node.this, str) else str(node.this)


def _attribute_call_name(node: exp.Expression) -> str | None:
    """The name PostgreSQL may call in attribute notation at ``node``, else ``None``.

    A qualified column ``t.f`` (also ``s.t.f``, ``"t"."f"``) and a field selection ``(expr).f``
    (also ``(t).f``, ``(t.*).f``) mean the call ``f(t)`` / ``f(expr)`` when ``f`` is not a column
    of ``t``. An unqualified name is never a call (PostgreSQL reports a missing column).
    """
    if isinstance(node, exp.Column):
        qualified = any(node.args.get(part) is not None for part in ("table", "db", "catalog"))
        name = node.this
        return _identifier_text(name) if qualified and isinstance(name, exp.Identifier) else None
    if isinstance(node, exp.Dot) and isinstance(node.expression, exp.Identifier):
        return _identifier_text(node.expression)
    return None


def _validate_tree(
    stmt: exp.Expression, engine: Engine, params: dict[str, JsonScalar]
) -> dict[str, int]:
    """Refuse the first rule violation; returns value-free counts for the console checklist."""
    counts = {"identifiers": 0, "literals": 0, "placeholders": 0}
    ident_re = ORACLE_IDENT if engine == "oracle" else PG_IDENT
    for node in _walk(stmt):
        if _INTO and isinstance(node, _INTO):
            raise SelectionError("selection_into", "SELECT … INTO is not allowed")
        if _LOCK and isinstance(node, _LOCK):
            raise SelectionError(
                "selection_for_update",
                "FOR UPDATE/SHARE is not allowed: DPS never locks source rows",
            )
        if isinstance(node, exp.Parameter):
            raise SelectionError(
                "selection_positional_param", "use named binds (:name), not positional ones"
            )
        if _UNSUPPORTED_STRINGS and isinstance(node, _UNSUPPORTED_STRINGS):
            raise SelectionError(
                "selection_literal_unsupported",
                "use plain quoted strings or named binds (no E'', B'', X'' or U&'' literals)",
            )
        if isinstance(node, exp.Func):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if name.startswith(DENIED_FUNCTION_PREFIXES):
                raise SelectionError("selection_function_denied", "a denied function is used")
        called = _attribute_call_name(node)
        if called is not None and called.lower().startswith(DENIED_ATTRIBUTE_PREFIXES):
            raise SelectionError(
                "selection_function_denied",
                "a denied function is called through attribute notation (name.function)",
            )
        if isinstance(node, exp.Placeholder):
            bind = str(node.name or "")
            if not _BIND_NAME.fullmatch(bind):
                raise SelectionError("selection_positional_param", "binds must be named :name")
            if bind not in params:
                raise SelectionError(
                    "selection_param_missing", "a named bind has no value in params"
                )
            counts["placeholders"] += 1
        if _is_value_literal_type(node) and not _structural(node):
            counts["literals"] += 1
        if isinstance(node, exp.Identifier):
            text = _identifier_text(node)
            if text == _WRAPPER and isinstance(node.parent, exp.Table):
                continue
            if text.lower().startswith(_RESERVED_PREFIX):
                raise SelectionError(
                    "selection_identifier_invalid", "identifiers starting with __dps_ are reserved"
                )
            column_name = isinstance(node.parent, exp.Column) and node.arg_key == "this"
            lowered = text.lower()
            if not column_name and (
                lowered in _DENY_SCHEMAS or lowered.startswith(_PACKAGE_PREFIXES)
            ):
                raise SelectionError(
                    "selection_function_denied", "a denied package or schema is referenced"
                )
            if not ident_re.fullmatch(text):
                raise SelectionError("selection_identifier_invalid", "an identifier is not valid")
            if column_name:
                counts["identifiers"] += 1
    return counts


def _fold(stmt: exp.Expression, engine: Engine) -> None:
    """Fold unquoted identifiers (Oracle upper, PostgreSQL lower) and quote every identifier."""
    for node in _walk(stmt):
        if isinstance(node, exp.Identifier):
            text = _identifier_text(node)
            if text.lower().startswith(_SENTINEL) or text == _WRAPPER:
                continue
            if not node.args.get("quoted"):
                node.set("this", text.upper() if engine == "oracle" else text.lower())
            node.set("quoted", True)


def _sentinel(name: str) -> exp.Column:
    return exp.column(f"{_SENTINEL}{name}")


def _number(text: str) -> JsonScalar:
    return int(text) if re.fullmatch(r"-?\d+", text) else text  # decimals stay text


def _bind_literals(
    stmt: exp.Expression, taken: set[str], engine: Engine
) -> tuple[exp.Expression, dict[str, JsonScalar]]:
    """Replace literal values by fresh binds and binds by sentinels; returns the new tree."""
    values: dict[str, JsonScalar] = {}
    counter = 0
    text_type = exp.DataType.build(_TEXT_TYPE[engine])

    def fresh() -> str:
        nonlocal counter
        while True:
            counter += 1
            name = f"l{counter}"
            if name not in taken:
                return name

    def transform(node: exp.Expression) -> exp.Expression:
        if _UNSUPPORTED_STRINGS and isinstance(node, _UNSUPPORTED_STRINGS):
            raise SelectionError(
                "selection_literal_unsupported",
                "use plain quoted strings or named binds (no E'', B'', X'' or U&'' literals)",
            )
        if isinstance(node, exp.Interval) and isinstance(node.this, exp.Literal):
            unit = node.args.get("unit")
            unit_name = unit.name.upper() if unit is not None else ""
            name = fresh()
            if engine == "oracle":
                singular = unit_name[:-1] if unit_name.endswith("S") else unit_name
                if singular in _ORACLE_DS_UNITS:
                    func = "NUMTODSINTERVAL"
                elif singular in _ORACLE_YM_UNITS:
                    func = "NUMTOYMINTERVAL"
                else:
                    raise SelectionError(
                        "selection_literal_unsupported",
                        "Oracle intervals need one unit (YEAR, MONTH, DAY, HOUR, MINUTE, SECOND)",
                    )
                values[name] = _number(str(node.this.this))
                return exp.Anonymous(
                    this=func, expressions=[_sentinel(name), exp.Literal.string(singular)]
                )
            values[name] = f"{node.this.this} {unit_name}".strip()
            return exp.Cast(
                this=exp.Cast(this=_sentinel(name), to=text_type.copy()),
                to=exp.DataType.build("interval"),
            )
        if _is_value_literal_type(node):
            if _structural(node):
                return node
            name = fresh()
            is_string = not isinstance(node, exp.Literal) or node.is_string
            values[name] = str(node.this) if is_string else _number(str(node.this))
            if isinstance(node.parent, exp.Cast) and node.parent.this is node:
                return exp.Cast(this=_sentinel(name), to=text_type.copy())
            return _sentinel(name)
        if isinstance(node, exp.Placeholder):
            return _sentinel(str(node.name))
        return node

    return stmt.transform(transform), values


def _render(node: exp.Expression, engine: Engine, *, style: str, order: list[str]) -> str:
    """SQL text with sentinels swapped for ``$n`` (PG exec) or ``:name`` (Oracle, templates).

    ``order`` receives the bind names in order of first appearance.
    """
    sql = node.sql(dialect=_DIALECT[engine])

    def repl(match: re.Match[str]) -> str:
        name = match.group(1).lower()
        if name not in order:
            order.append(name)
        if style == "template" or engine == "oracle":
            return f":{name}"
        return f"${order.index(name) + 1}"

    return _SENTINEL_RE.sub(repl, sql)


def _where_expr(stmt: exp.Expression) -> exp.Expression:
    where = stmt.args.get("where")
    if where is None:  # pragma: no cover - the wrapper always has one
        raise SelectionError("selection_parse_error", "the where clause is empty")
    return where.this


def parameterize_literals(sel: Selection, engine: Engine) -> Selection:
    """Move every literal value into ``params`` (``l1``, ``l2``…); the text keeps ``:name`` binds.

    Validates as it goes (the same rules as :func:`validate_selection` without a shape).
    MongoDB selections are returned unchanged (their values live in ``mongo_filter``).
    """
    if engine == "mongo":
        return sel
    params: dict[str, JsonScalar] = dict(sel.params)
    taken = set(params)
    update: dict[str, Any] = {}
    for field, wrap in (("where", True), ("query", False)):
        text = getattr(sel, field)
        if text is None:
            continue
        stmt = _parse(text, engine, wrap_where=wrap)
        _validate_tree(stmt, engine, params)
        _fold(stmt, engine)
        new, values = _bind_literals(stmt, taken, engine)
        taken |= set(values)
        params.update(values)
        target = _where_expr(new) if wrap else new
        update[field] = _render(target, engine, style="template", order=[])
    update["params"] = params
    return sel.model_copy(update=update)


def _compile(
    text: str, engine: Engine, params: dict[str, JsonScalar], *, wrap: bool
) -> tuple[str, list[str]]:
    stmt = _parse(text, engine, wrap_where=wrap)
    _validate_tree(stmt, engine, params)
    if _unbound_literals(stmt):
        raise SelectionError(
            "selection_not_parameterized", "literals must be bound before compiling"
        )
    _fold(stmt, engine)
    new, _ = _bind_literals(stmt, set(params), engine)
    order: list[str] = []
    target = _where_expr(new) if wrap else new
    return _render(target, engine, style="exec", order=order), order


def compile_where(sel: Selection, engine: Engine) -> tuple[str, list[str]]:
    """``(fragment, ordered bind names)``; ``("", [])`` without a where.

    PostgreSQL fragments use ``$n`` (position = index in the names + 1); Oracle uses ``:name``.
    """
    return ("", []) if sel.where is None else _compile(sel.where, engine, sel.params, wrap=True)


def compile_query(sel: Selection, engine: Engine) -> tuple[str, list[str]]:
    """Full read-only SELECT with binds; ``("", [])`` without a query."""
    return ("", []) if sel.query is None else _compile(sel.query, engine, sel.params, wrap=False)


def where_template(text: str, engine: Engine) -> str:
    """Value-free form of a where clause (literals → ``:lN``) for ``dps_job.spec`` and evidence."""
    stmt = _parse(text, engine, wrap_where=True)
    _fold(stmt, engine)
    new, _ = _bind_literals(stmt, set(), engine)
    return _render(_where_expr(new), engine, style="template", order=[])


def query_template(text: str, engine: Engine) -> str:
    """Value-free form of a query."""
    stmt = _parse(text, engine, wrap_where=False)
    _fold(stmt, engine)
    new, _ = _bind_literals(stmt, set(), engine)
    return _render(new, engine, style="template", order=[])


def referenced_columns(sel: Selection, engine: Engine) -> set[str]:
    """Folded names of the object's columns a where clause references.

    Columns inside a subquery belong to that subquery's tables and are not included.
    """
    if sel.where is None:
        return set()
    stmt = _parse(sel.where, engine, wrap_where=True)
    _fold(stmt, engine)
    return {
        c.name
        for c in _walk(_where_expr(stmt))
        if isinstance(c, exp.Column)
        and not c.name.lower().startswith(_SENTINEL)
        and c.find_ancestor(exp.Select) is stmt
    }


def query_columns(sel: Selection, engine: Engine) -> set[str] | None:
    """The folded output names of ``sel.query``; None without a query or when it projects ``*``
    (bare or qualified)."""
    if sel.query is None:
        return None
    stmt = _parse(sel.query, engine, wrap_where=False)
    _fold(stmt, engine)
    names: set[str] = set()
    for proj in stmt.expressions:
        if isinstance(proj, exp.Star) or (
            isinstance(proj, exp.Column) and isinstance(proj.this, exp.Star)
        ):
            return None
        names.add(proj.alias_or_name)
    return names


def query_projects_keys(sel: Selection, engine: Engine, key: list[str]) -> bool:
    """True when the query's projection includes ``*`` or every key column."""
    names = query_columns(sel, engine)
    if names is None:
        return True
    folded = {k.upper() if engine == "oracle" else k for k in key}
    return folded <= names


def read_projection(shape: TableShape, sel: Selection, engine: Engine) -> list[ColumnInfo]:
    """The columns a read of ``sel`` yields, in table order — and so what a run writes.

    Every column of ``shape``, narrowed by a query's own select list (a query that names its
    columns yields only those) and by ``columns``; the key columns always. Connectors read and
    the run handler plans, creates and writes exactly this list, so a column outside it is
    never read, created or NULL-filled (M3 G2 B1). Oracle folds ``columns`` to upper case.
    """
    names = query_columns(sel, engine)
    if sel.columns:
        wanted = {n.upper() if engine == "oracle" else n for n in sel.columns}
        names = wanted if names is None else names & wanted
    if names is None:
        return list(shape.columns)
    names |= set(shape.key)
    return [c for c in shape.columns if c.name in names]


def validate_selection(sel: Selection, engine: Engine, shape: TableShape | None) -> None:
    """Raise the first :class:`SelectionError`; ``shape`` (when given) enables column/key checks."""
    if engine == "mongo":
        if sel.where is not None or sel.query is not None:
            raise SelectionError("selection_engine_mismatch", "MongoDB selections use mongo_filter")
        if sel.mongo_filter is not None:
            validate_mongo_filter(sel.mongo_filter)
        for name in sel.columns or []:
            quote_ident(name, engine)
        return
    if sel.mongo_filter is not None:
        raise SelectionError("selection_engine_mismatch", "mongo_filter applies to MongoDB only")
    for field, wrap in (("where", True), ("query", False)):
        text = getattr(sel, field)
        if text is not None:
            _validate_tree(_parse(text, engine, wrap_where=wrap), engine, sel.params)
    for name in sel.columns or []:
        quote_ident(name, engine)
    if shape is not None:
        known = {c.name.upper() if engine == "oracle" else c.name for c in shape.columns}
        if referenced_columns(sel, engine) - known:
            raise SelectionError(
                "selection_unknown_column",
                "the where clause names a column the object does not have",
            )
        for name in sel.columns or []:
            if (name.upper() if engine == "oracle" else name) not in known:
                raise SelectionError(
                    "selection_unknown_column", "a projected column does not exist"
                )
        if not query_projects_keys(sel, engine, shape.key):
            raise SelectionError(
                "selection_query_missing_key", "the query must project every key column"
            )


_CHECKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("one_statement", ("selection_multiple_statements", "selection_parse_error")),
    ("select_only", ("selection_not_select", "selection_dml", "selection_engine_mismatch")),
    (
        "no_into_lock_procs",
        (
            "selection_into",
            "selection_for_update",
            "selection_function_denied",
            "selection_positional_param",
            "selection_operator_denied",
        ),
    ),
    ("identifiers_validated", ("selection_identifier_invalid", "selection_unknown_column")),
    (
        "literals_bound",
        ("selection_param_missing", "selection_not_parameterized", "selection_literal_unsupported"),
    ),
    ("keyset_able", ("selection_query_missing_key",)),
)


def check_for(code: str) -> str | None:
    """The checklist entry (``one_statement``, ``literals_bound`` …) a selection code fails."""
    for name, codes in _CHECKS:
        if code in codes:
            return name
    return None


def failed_checks(
    sel: Selection, engine: Engine, shape: TableShape | None, code: str
) -> list[dict[str, Any]]:
    """:func:`selection_checks` for a selection refused with ``code`` (by binding, validation or
    compilation): the entry ``code`` belongs to is failed even when re-validating the unbound
    selection would not fail it."""
    try:
        checks = selection_checks(sel, engine, shape)
    except SelectionError:  # the counting pass re-parses: report the refusal alone
        checks = [{"name": name, "ok": True} for name, _ in _CHECKS]
    name = check_for(code)
    for entry in checks:
        if entry["name"] == name and entry["ok"]:
            entry["ok"], entry["code"] = False, code
    return checks


def selection_checks(
    sel: Selection, engine: Engine, shape: TableShape | None
) -> list[dict[str, Any]]:
    """The wizard's checklist (mockup 06 step 2): every check ok, or the failing one named.

    Each entry is ``{"name", "ok", "code"?, "count"?}`` — names, codes and counts only.
    """
    failed: str | None = None
    try:
        validate_selection(sel, engine, shape)
    except SelectionError as exc:
        failed = exc.code
    counts = {"identifiers": 0, "literals": 0, "placeholders": 0}
    if failed is None and engine != "mongo":
        for field, wrap in (("where", True), ("query", False)):
            text = getattr(sel, field)
            if text is not None:
                c = _validate_tree(_parse(text, engine, wrap_where=wrap), engine, sel.params)
                counts = {k: counts[k] + c[k] for k in counts}
    out: list[dict[str, Any]] = []
    for name, codes in _CHECKS:
        entry: dict[str, Any] = {"name": name, "ok": failed not in codes}
        if not entry["ok"]:
            entry["code"] = failed
        if name == "identifiers_validated":
            entry["count"] = counts["identifiers"]
        if name == "literals_bound":
            entry["count"] = counts["placeholders"]
        out.append(entry)
    return out


def validate_mongo_filter(flt: dict[str, Any], _depth: int = 0) -> None:
    """Operator allow-list (``$where``, ``$expr``, ``$function`` … refused) and field-path rules."""
    if _depth > 20:
        raise SelectionError("selection_operator_denied", "filter nesting is too deep")
    for key, value in flt.items():
        if not isinstance(key, str):
            raise SelectionError("selection_identifier_invalid", "filter keys must be strings")
        if key.startswith("$"):
            if key not in MONGO_OPERATORS:
                raise SelectionError(
                    "selection_operator_denied", "a MongoDB operator outside the allow-list is used"
                )
        elif not MONGO_PATH.fullmatch(key):
            raise SelectionError("selection_identifier_invalid", "not a valid MongoDB field path")
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, dict):
                validate_mongo_filter(item, _depth + 1)


def mongo_filter_template(flt: dict[str, Any]) -> dict[str, Any]:
    """Every scalar value replaced by ``"?"`` (structure and operators kept)."""

    def walk(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        return "?"

    return {k: walk(v) for k, v in flt.items()}
