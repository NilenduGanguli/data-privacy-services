"""Selection validation, literal binding, compilation and templates (contract §5.2)."""

from __future__ import annotations

import pytest

from dps.jobs_models import ObjectRef, Selection
from dps.layers.connectors.base import ColumnInfo, TableShape
from dps.layers.connectors.selection import (
    DENIED_FUNCTION_PREFIXES,
    SelectionError,
    compile_query,
    compile_where,
    mongo_filter_template,
    parameterize_literals,
    query_projects_keys,
    query_template,
    quote_ident,
    referenced_columns,
    selection_checks,
    validate_mongo_filter,
    validate_selection,
    where_template,
)

SHAPE = TableShape(
    object=ObjectRef(owner="bank", name="customers"),
    key=["customer_id"],
    key_source="primary_key",
    columns=[
        ColumnInfo(path=f"$.{n}", name=n, data_type="text", kind="text")
        for n in ("customer_id", "created_at", "country_code", "full_name", "risk_score")
    ],
)


def test_quote_ident_rules() -> None:
    assert quote_ident("created_at", "postgres") == '"created_at"'
    assert quote_ident("Created_At", "oracle") == '"CREATED_AT"'
    assert quote_ident("profile.contact.email", "mongo") == "profile.contact.email"
    for name, engine in (
        ("bad name", "postgres"),
        ("1abc", "oracle"),
        ('x"y', "postgres"),
        ("a..b", "mongo"),
    ):
        with pytest.raises(SelectionError) as err:
            quote_ident(name, engine)  # type: ignore[arg-type]
        assert err.value.code == "selection_identifier_invalid"


def test_parameterize_then_compile_postgres() -> None:
    sel = parameterize_literals(
        Selection(
            where="CREATED_AT >= DATE '2025-01-01' AND country_code IN ('IN', 'US') "
            "AND risk_score > :min_risk AND created_at > now() - INTERVAL '30 days'",
            params={"min_risk": 500},
        ),
        "postgres",
    )
    assert {k: sel.params[k] for k in ("min_risk", "l1", "l2", "l3")} == {
        "min_risk": 500,
        "l1": "2025-01-01",
        "l2": "IN",
        "l3": "US",
    }
    assert str(sel.params["l4"]).lower() == "30 days"  # sqlglot may split the unit; PG accepts both
    assert sel.where is not None
    assert "2025" not in sel.where and "'IN'" not in sel.where and ":l1" in sel.where
    fragment, names = compile_where(sel, "postgres")
    assert names == ["l1", "l2", "l3", "min_risk", "l4"]
    assert '"created_at" >= CAST(CAST($1 AS TEXT) AS DATE)' in fragment
    assert '"country_code" IN ($2, $3)' in fragment and '"risk_score" > $4' in fragment
    assert "CAST(CAST($5 AS TEXT) AS INTERVAL)" in fragment


def test_repeated_bind_reuses_its_position() -> None:
    fragment, names = compile_where(
        Selection(where="a = :x OR b = :x", params={"x": 1}), "postgres"
    )
    assert names == ["x"] and fragment.count("$1") == 2


def test_oracle_rendering_uses_named_binds_and_upper_identifiers() -> None:
    sel = parameterize_literals(Selection(where="created_at >= DATE '2025-01-01'"), "oracle")
    fragment, names = compile_where(sel, "oracle")
    assert names == ["l1"] and '"CREATED_AT"' in fragment and ":l1" in fragment


def test_unparameterized_literals_are_refused_at_compile() -> None:
    with pytest.raises(SelectionError) as err:
        compile_where(Selection(where="full_name = 'Ananya'"), "postgres")
    assert err.value.code == "selection_not_parameterized" and "Ananya" not in str(err.value)


@pytest.mark.parametrize(
    "sel,code",
    [
        (Selection(where="1 = 1; DROP TABLE x"), "selection_multiple_statements"),
        (Selection(query="INSERT INTO t VALUES (1)"), "selection_not_select"),
        (Selection(query="SELECT * INTO new_t FROM bank.customers"), "selection_into"),
        (Selection(query="SELECT * FROM bank.customers FOR UPDATE"), "selection_for_update"),
        (
            Selection(query="WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x"),
            "selection_dml",
        ),
        (Selection(where="pg_sleep(10) IS NOT NULL"), "selection_function_denied"),
        (
            Selection(where="set_config('statement_timeout', '0', false) = '0'"),
            "selection_function_denied",
        ),
        (Selection(where="a = $1"), "selection_positional_param"),
        (Selection(where="full_name = :who"), "selection_param_missing"),
        (Selection(where="nonexistent = 1"), "selection_unknown_column"),
        (Selection(where="full_name = (SELECT"), "selection_parse_error"),
        (Selection(query="SELECT full_name FROM bank.customers"), "selection_query_missing_key"),
    ],
)
def test_rejections(sel: Selection, code: str) -> None:
    with pytest.raises(SelectionError) as err:
        validate_selection(sel, "postgres", SHAPE)
    assert err.value.code == code


def test_oracle_package_calls_are_denied() -> None:
    with pytest.raises(SelectionError) as err:
        validate_selection(Selection(where="dbms_lock.sleep(5) = 1"), "oracle", None)
    assert err.value.code == "selection_function_denied"


def test_query_compile_template_and_keys() -> None:
    sel = parameterize_literals(
        Selection(
            query="SELECT c.customer_id, c.full_name FROM bank.customers c WHERE c.country_code = 'IN'"
        ),
        "postgres",
    )
    sql, names = compile_query(sel, "postgres")
    assert names == ["l1"] and "$1" in sql and "'IN'" not in sql
    assert query_projects_keys(sel, "postgres", ["customer_id"])
    assert "'IN'" not in query_template("SELECT customer_id FROM t WHERE x = 'IN'", "postgres")


def test_templates_and_referenced_columns_are_value_free() -> None:
    template = where_template("full_name = 'Ananya Krishnan' AND risk_score > 700", "postgres")
    assert "Ananya" not in template and "700" not in template
    assert ":l1" in template and ":l2" in template
    assert referenced_columns(
        Selection(where="FULL_NAME = :a AND risk_score > :b", params={"a": 1, "b": 2}), "postgres"
    ) == {"full_name", "risk_score"}


def test_selection_checks_for_the_console() -> None:
    sel = parameterize_literals(
        Selection(where="created_at >= DATE '2025-01-01' AND country_code IN ('IN','US')"),
        "postgres",
    )
    checks = {c["name"]: c for c in selection_checks(sel, "postgres", SHAPE)}
    assert all(c["ok"] for c in checks.values())
    assert checks["literals_bound"]["count"] == 3 and checks["identifiers_validated"]["count"] == 2
    bad = {
        c["name"]: c
        for c in selection_checks(Selection(query="SELECT * FROM t FOR UPDATE"), "postgres", SHAPE)
    }
    assert bad["no_into_lock_procs"]["ok"] is False


def test_mongo_filter_allow_list_and_template() -> None:
    flt = {
        "$and": [
            {"kyc.status": {"$in": ["PENDING", "REVIEW"]}},
            {"age": {"$gte": 18}},
            {"accounts": {"$elemMatch": {"iban": {"$exists": True}}}},
        ]
    }
    validate_mongo_filter(flt)
    assert mongo_filter_template(flt) == {
        "$and": [
            {"kyc.status": {"$in": ["?", "?"]}},
            {"age": {"$gte": "?"}},
            {"accounts": {"$elemMatch": {"iban": {"$exists": "?"}}}},
        ]
    }
    for bad in (
        {"$where": "sleep(1)"},
        {"a": {"$expr": {}}},
        {"a": {"$function": {}}},
        {"bad key": 1},
    ):
        with pytest.raises(SelectionError) as err:
            validate_mongo_filter(bad)
        assert err.value.code in ("selection_operator_denied", "selection_identifier_invalid")


# --- hardening beyond the plan (see M2-DEVIATIONS, Task 12) ---------------------------------


def test_oracle_intervals_and_timestamps_compile_with_only_structural_literals() -> None:
    sel = parameterize_literals(
        Selection(
            where="created_at > SYSDATE - INTERVAL '30' DAY "
            "AND updated_at >= TIMESTAMP '2025-01-01 10:00:00'"
        ),
        "oracle",
    )
    assert sel.params == {"l1": 30, "l2": "2025-01-01 10:00:00"}
    assert sel.where is not None and "NUMTODSINTERVAL(:l1, 'DAY')" in sel.where
    fragment, names = compile_where(sel, "oracle")
    assert names == ["l1", "l2"] and "2025" not in fragment and "30" not in fragment
    months = parameterize_literals(Selection(where="d > SYSDATE - INTERVAL '2' MONTH"), "oracle")
    assert months.where is not None and "NUMTOYMINTERVAL(:l1, 'MONTH')" in months.where


def test_type_parameters_and_order_positions_stay_in_the_text() -> None:
    sel = parameterize_literals(Selection(where="risk_score::numeric(10,2) > 5"), "postgres")
    assert sel.params == {"l1": 5}
    fragment, names = compile_where(sel, "postgres")
    assert names == ["l1"] and "(10, 2)" in fragment and "> $1" in fragment
    query, binds = compile_query(
        Selection(query="SELECT customer_id FROM bank.customers ORDER BY 1"), "postgres"
    )
    assert binds == [] and query.endswith("ORDER BY 1")


def test_comments_and_dollar_quoted_strings_never_reach_a_template() -> None:
    sel = parameterize_literals(
        Selection(where="full_name = $$Ananya Krishnan$$ -- the customer Ananya\n"), "postgres"
    )
    assert sel.params == {"l1": "Ananya Krishnan"}
    assert sel.where == '"full_name" = :l1'
    assert "Ananya" not in where_template("x = 1 /* Ananya */ AND y = N'Ananya'", "postgres")


@pytest.mark.parametrize("where", ["x = E'Anan\\x79a'", "x = U&'d\\0061t'", "x = B'101'"])
def test_escaped_string_forms_are_refused_without_quoting_them(where: str) -> None:
    for call in (
        lambda: validate_selection(Selection(where=where), "postgres", None),
        lambda: where_template(where, "postgres"),
    ):
        with pytest.raises(SelectionError) as err:
            call()
        assert err.value.code == "selection_literal_unsupported" and "Anan" not in str(err.value)


@pytest.mark.parametrize(
    "where,code",
    [
        ("full_name = :a ORDER BY 1", "selection_parse_error"),
        ("full_name = :a LIMIT 5", "selection_parse_error"),
        ("1 = 1 UNION SELECT * FROM bank.secrets", "selection_not_select"),
        ("__dps_bind_a = 1", "selection_identifier_invalid"),
        (
            "query_to_xml('select pg_sleep(9)', true, true, '') IS NOT NULL",
            "selection_function_denied",
        ),
        ("pg_try_advisory_lock(1)", "selection_function_denied"),
        ("pg_catalog.pg_sleep(1) IS NULL", "selection_function_denied"),
        ('"bad name" = 1', "selection_identifier_invalid"),
    ],
)
def test_more_rejections(where: str, code: str) -> None:
    with pytest.raises(SelectionError) as err:
        validate_selection(Selection(where=where, params={"a": 1}), "postgres", None)
    assert err.value.code == code


#: Calls of functions that execute a SQL string: core PostgreSQL ``ts_stat``/``ts_rewrite``,
#: tablefunc's ``crosstab*``/``connectby``, xml2's ``xpath_table`` and pg_background. Binding the
#: string as a parameter
#: would run any statement (``set_config``, ``pg_sleep``, ``dblink_exec``…) past validation.
_SQL_STRING_CALLS = [
    "ts_stat('select 1')",
    "TS_STAT('select 1')",
    "Ts_Stat('select 1', 'ab')",
    "pg_catalog.ts_stat('select 1')",
    "PG_CATALOG.TS_STAT('select 1')",
    "\"ts_stat\"('select 1')",
    "pg_catalog.\"ts_stat\"('select 1')",
    '"pg_catalog"."ts_stat"(\'select 1\')',
    "ts_stat(sqlquery => 'select 1')",
    "ts_rewrite('a'::tsquery, 'select 1')",
    "TS_REWRITE('a'::tsquery, 'select 1')",
    "pg_catalog.ts_rewrite('a'::tsquery, 'select 1')",
    "\"pg_catalog\".\"ts_rewrite\"('a'::tsquery, 'select 1')",
    "crosstab('select 1')",
    "CROSSTAB('select 1', 'select 2')",
    "crosstab2('select 1')",
    "public.crosstab('select 1', 2)",
    '"public"."crosstab"(\'select 1\')',
    "connectby('t', 'a', 'b', '1', 0)",
    "CONNECTBY('t', 'a', 'b', 'o', '1', 0, '~')",
    "public.\"connectby\"('t', 'a', 'b', '1', 0)",
    "pg_background_launch('select 1')",
    "PUBLIC.PG_BACKGROUND_LAUNCH('select 1')",
    "xpath_table('k', 'd', '(select 1 as k) s', '/a', 'true')",
    "XPATH_TABLE('k', 'd', '(select 1 as k) s', '/a', 'true')",
    "Xpath_Table('k', 'd', 't', '/a', 'select 1')",
    "public.xpath_table('k', 'd', '(select 1 as k) s', '/a', 'true')",
    "PUBLIC.XPATH_TABLE('k', 'd', '(select 1 as k) s', '/a', 'true')",
    "\"xpath_table\"('k', 'd', '(select 1 as k) s', '/a', 'true')",
    "public.\"xpath_table\"('k', 'd', '(select 1 as k) s', '/a', 'true')",
    "\"public\".\"xpath_table\"('k', 'd', '(select 1 as k) s', '/a', 'true')",
]


@pytest.mark.parametrize("call", _SQL_STRING_CALLS)
def test_functions_that_run_a_sql_string_are_denied_in_where_and_query_form(call: str) -> None:
    selections = [
        Selection(where=f"{call} IS NOT NULL"),
        Selection(where=f"EXISTS (SELECT 1 FROM {call} AS f)"),
        Selection(query=f"SELECT * FROM {call} AS f(a text)"),
        Selection(query=f"SELECT * FROM ROWS FROM ({call}) AS f"),
        Selection(query=f"SELECT customer_id, {call} AS v FROM bank.customers"),
        Selection(
            query=f"SELECT c.customer_id FROM bank.customers c, LATERAL {call} AS f "
            "WHERE c.country_code = 'IN'"
        ),
    ]
    for sel in selections:
        for check in (
            lambda s=sel: validate_selection(s, "postgres", None),
            lambda s=sel: parameterize_literals(s, "postgres"),
        ):
            with pytest.raises(SelectionError) as err:
                check()
            assert err.value.code == "selection_function_denied", (sel, err.value.code)
            assert "select 1" not in str(err.value)


@pytest.mark.parametrize(
    "call",
    [
        "ts_stat(:q)",
        "pg_catalog.TS_STAT(:q, :q)",
        '"ts_stat"(:q)',
        "ts_rewrite(CAST(:q AS tsquery), :q)",
        'pg_catalog."ts_rewrite"(CAST(:q AS tsquery), :q)',
        "crosstab(:q)",
        "CROSSTAB4(:q)",
        "connectby(:q, :q, :q, :q, :q)",
        "pg_background_launch(:q)",
        "xpath_table(:q, :q, :q, :q, :q)",
        "public.XPATH_TABLE(:q, :q, :q, :q, :q)",
        '"public"."xpath_table"(:q, :q, :q, :q, :q)',
    ],
)
def test_a_bound_sql_string_never_compiles_into_a_sql_executing_function(call: str) -> None:
    params = {"q": "select set_config('statement_timeout', '0', true)::tsvector"}
    for compile_sel, sel in (
        (compile_where, Selection(where=f"{call} IS NOT NULL", params=params)),
        (compile_query, Selection(query=f"SELECT * FROM {call} AS f(a text)", params=params)),
    ):
        with pytest.raises(SelectionError) as err:
            compile_sel(sel, "postgres")
        assert err.value.code == "selection_function_denied"
        assert "statement_timeout" not in str(err.value)


def test_the_sql_string_functions_are_on_the_exported_deny_list() -> None:
    for name in (
        "ts_stat",
        "ts_rewrite",
        "crosstab",
        "connectby",
        "pg_background_launch",
        "xpath_table",
    ):
        assert name.startswith(DENIED_FUNCTION_PREFIXES)


def test_the_xpath_table_relation_attack_is_refused_at_every_stage() -> None:
    # The review G3 r2 reproduction: xpath_table builds 'SELECT key, doc FROM <relation> WHERE
    # <criteria>' from its text arguments, so a bound relation runs set_config/pg_sleep.
    params = {
        "rel": "(select 1 as k, '<a>' || set_config('statement_timeout', '0', true) "
        "|| '</a>' as d) s"
    }
    where = Selection(
        where="EXISTS (SELECT 1 FROM xpath_table('k', 'd', :rel, '/a', 'true') "
        "AS t(k int, a text))",
        params=params,
    )
    query = Selection(
        query="SELECT * FROM public.xpath_table('k', 'd', :rel, '/a', 'true') AS t(k int, a text)",
        params=params,
    )
    for sel, compile_sel in ((where, compile_where), (query, compile_query)):
        for check in (
            lambda s=sel: validate_selection(s, "postgres", None),
            lambda s=sel: parameterize_literals(s, "postgres"),
            lambda s=sel, c=compile_sel: c(s, "postgres"),
        ):
            with pytest.raises(SelectionError) as err:
                check()
            assert err.value.code == "selection_function_denied"
            assert "statement_timeout" not in str(err.value)


def test_xml_functions_that_take_a_document_are_not_mistaken_for_xpath_table() -> None:
    # Core xpath()/xpath_exists() and xml2's document functions read a document, not SQL.
    for call in (
        "xpath('/a', CAST(:d AS xml)) IS NOT NULL",
        "xpath_exists('/a', CAST(:d AS xml))",
        "xpath_string(:d, '/a') IS NOT NULL",
        "xpath_bool(:d, '/a')",
    ):
        validate_selection(Selection(where=call, params={"d": "<a/>"}), "postgres", None)


def test_oracle_hierarchical_queries_are_not_mistaken_for_connectby() -> None:
    validate_selection(
        Selection(
            query="SELECT customer_id, SYS_CONNECT_BY_PATH(full_name, '/') AS p "
            "FROM bank.customers START WITH customer_id = :r "
            "CONNECT BY PRIOR customer_id = parent_id",
            params={"r": 1},
        ),
        "oracle",
        None,
    )


def test_column_names_are_not_package_names_and_subqueries_have_their_own_scope() -> None:
    validate_selection(Selection(where="utl_rate > :a", params={"a": 1}), "postgres", None)
    validate_selection(
        Selection(
            where="customer_id IN (SELECT a.customer_id FROM bank.accounts a WHERE a.balance > :b)",
            params={"b": 5},
        ),
        "postgres",
        SHAPE,
    )
    with pytest.raises(SelectionError) as err:
        validate_selection(Selection(where="sys.secret = 1"), "oracle", None)
    assert err.value.code == "selection_function_denied"


def test_projected_columns_are_identifiers_of_the_object() -> None:
    validate_selection(Selection(columns=["full_name", "risk_score"]), "postgres", SHAPE)
    for columns, code in (
        (["bad name"], "selection_identifier_invalid"),
        (["nope"], "selection_unknown_column"),
    ):
        with pytest.raises(SelectionError) as err:
            validate_selection(Selection(columns=columns), "postgres", SHAPE)
        assert err.value.code == code


def test_mongo_selections_refuse_sql_and_sql_refuses_mongo_filters() -> None:
    with pytest.raises(SelectionError) as err:
        validate_selection(Selection(where="a = 1"), "mongo", None)
    assert err.value.code == "selection_engine_mismatch"
    with pytest.raises(SelectionError) as err2:
        validate_selection(Selection(mongo_filter={"a": 1}), "postgres", None)
    assert err2.value.code == "selection_engine_mismatch"
    assert parameterize_literals(Selection(mongo_filter={"a": 1}), "mongo").mongo_filter == {"a": 1}
