"""M2's sqlglot selection rules pinned for the Oracle dialect (offline)."""

from __future__ import annotations

import pytest

pytest.importorskip("sqlglot")
from dps.layers.connectors.selection import (  # noqa: E402
    Selection,
    SelectionError,
    compile_where,
    parameterize_literals,
    quote_ident,
    validate_selection,
)

ALLOWED = [
    "CREATED_AT >= DATE '2025-01-01'",
    "SEGMENT = :seg AND RISK_SCORE BETWEEN :lo AND :hi",
    "CREATED_AT >= TO_DATE(:d, 'YYYY-MM-DD')",
    "UPPER(CITY) IN ('PUNE', 'MUMBAI')",
]
REJECTED = [
    "1 = 1; DROP TABLE CRM.CUSTOMERS",
    "SEGMENT = 'x' FOR UPDATE",
    "DBMS_PIPE.RECEIVE_MESSAGE('a', 10) = 0",
    "UTL_HTTP.REQUEST('http://x') IS NOT NULL",
    "SYS.DBMS_LOCK.SLEEP(5) IS NULL",
    "XMLTYPE('<a/>') IS NOT NULL",
    "HTTPURITYPE('http://x').GETCLOB() IS NOT NULL",
    "CUSTOMER_ID IN (SELECT CUSTOMER_ID FROM CRM.CUSTOMERS FOR UPDATE)",
]
PARAMS = {"seg": "retail", "lo": 1, "hi": 9, "d": "2025-01-01"}


@pytest.mark.parametrize("where", ALLOWED)
def test_oracle_where_allowed(where: str) -> None:
    validate_selection(Selection(where=where, params=PARAMS), "oracle", None)


@pytest.mark.parametrize("where", REJECTED)
def test_oracle_where_rejected(where: str) -> None:
    with pytest.raises(SelectionError):
        validate_selection(Selection(where=where), "oracle", None)


def test_oracle_binds_are_named_and_ordered() -> None:
    sel = Selection(where="SEGMENT = :seg AND RISK_SCORE > :lo", params={"seg": "x", "lo": 1})
    fragment, names = compile_where(sel, "oracle")
    assert ":seg" in fragment and ":lo" in fragment and names == ["seg", "lo"]


def test_oracle_literals_become_binds_before_compilation() -> None:
    bound = parameterize_literals(Selection(where="SEGMENT = 'premium'"), "oracle")
    fragment, names = compile_where(bound, "oracle")
    assert "premium" not in fragment and names == list(bound.params)
    with pytest.raises(SelectionError) as info:
        compile_where(Selection(where="SEGMENT = 'premium'"), "oracle")
    assert info.value.code == "selection_not_parameterized"


def test_oracle_identifier_rule() -> None:
    assert quote_ident("crm", "oracle") == '"CRM"' and quote_ident("A$B#1", "oracle") == '"A$B#1"'
    for bad in ("1A", "A B", 'A"B', "x" * 129):
        with pytest.raises(SelectionError):
            quote_ident(bad, "oracle")
