"""Audit search across the caller's tenants, the action → console-layer mapping (shared with the
report roll-ups) and a formula-safe CSV export (M5, contract §9 ``GET /admin/audit``).

Each tenant is queried under its own GUC (RLS stays the only gate): one keyset-paged query per
tenant, merged by ``(ts, id)`` descending — ``id`` comes from one identity sequence, so the pair
orders globally. Audit rows are value-free by construction (route templates, action names, counts);
the CSV still guards every cell against spreadsheet formula injection, because a route template or
a policy label is text a spreadsheet would evaluate.
"""

from __future__ import annotations

import base64
import binascii
import csv
import io
from collections.abc import AsyncIterator, Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from dps import db

LAYERS = ("api", "conn", "ui", "admin")
OUTCOME_GROUPS: dict[str, list[str]] = {
    "ok": ["ok"],
    "failed": ["error", "failed", "verification_failed", "leakage", "dead", "upstream_error"],
    "blocked": ["rejected", "bad_request", "denied", "blocked", "forbidden", "canceled"],
}
CSV_HEADER = (
    "ts",
    "tenant_id",
    "key_name",
    "key_id",
    "route",
    "action",
    "layer",
    "mode",
    "policy",
    "findings",
    "methods",
    "outcome",
    "status",
    "job_id",
)
CSV_PAGE = 1000
_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def layer_case(column: str) -> str:
    """SQL ``CASE`` mapping an audit ``action`` to its console layer (shared with the roll-ups)."""
    return (
        f"CASE WHEN {column} LIKE 'job.%' OR {column} LIKE 'snapshot.%' "
        f"OR {column} LIKE 'connection.%' OR {column} LIKE 'group.%' "
        f"OR {column} LIKE 'evidence.%' THEN 'conn' "
        f"WHEN {column} LIKE 'scan.%' THEN 'ui' "
        f"WHEN {column} LIKE 'admin.%' OR {column} LIKE 'retention.%' THEN 'admin' ELSE 'api' END"
    )


class AuditRow(BaseModel):
    """One audit row as the Reports & audit page shows it (names, ids and counts only)."""

    id: int
    ts: datetime
    tenant_id: str
    key_id: str | None = None
    key_name: str | None = None
    trace_id: str | None = None
    route: str
    action: str
    layer: str
    mode: str | None = None
    policy: str | None = None
    outcome: str
    status: int | None = None
    job_id: str | None = None
    findings: int | None = None
    methods: dict[str, int] = Field(default_factory=dict)


def encode_cursor(ts: datetime, row_id: int) -> str:
    """Opaque keyset cursor: ``base64url("<iso>|<id>")``."""
    return base64.urlsafe_b64encode(f"{ts.isoformat()}|{row_id}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    """The ``(ts, id)`` of a cursor this API issued; ``ValueError`` otherwise (value-free)."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("ascii")
        ts, _, row_id = raw.partition("|")
        parsed = datetime.fromisoformat(ts)
        if parsed.tzinfo is None:
            raise ValueError("naive")
        return parsed, int(row_id)
    except (ValueError, UnicodeDecodeError, binascii.Error):
        raise ValueError("malformed audit cursor") from None


def like_escape(text: str) -> str:
    """Escape ``\\``, ``%`` and ``_`` for a ``LIKE … ESCAPE '\\'`` pattern."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def csv_cell(value: Any) -> str:
    """CSV formula-injection guard: a cell a spreadsheet would evaluate gets a leading quote."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA) else text


def _sql() -> str:
    s = db.schema_ident()
    layer = layer_case("action")
    return (
        "SELECT id, ts, tenant_id, key_id, trace_id, route, action, "
        f"{layer} AS layer, mode, policy, outcome, status, job_id, counts "
        f"FROM {s}.dps_audit_log "
        "WHERE tenant_id = $12 AND ts >= $1 AND ts < $2 "
        "AND ($3::text IS NULL OR action = $3 OR action LIKE $13 || '.%' ESCAPE '\\') "
        f"AND ($4::text IS NULL OR {layer} = $4) "
        "AND ($5::text[] IS NULL OR outcome = ANY($5)) "
        "AND ($6::text IS NULL OR job_id = $6 OR policy = $6 "
        "OR route ILIKE '%' || $7 || '%' ESCAPE '\\' OR key_id = ANY($8::text[])) "
        "AND ($9::timestamptz IS NULL OR (ts, id) < ($9, $10)) "
        "ORDER BY ts DESC, id DESC LIMIT $11"
    )


def _int_map(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {str(k): int(v) for k, v in value.items() if isinstance(v, int | float)}


def _row(r: Any, key_names: Mapping[str, str]) -> AuditRow:
    counts = r["counts"] if isinstance(r["counts"], dict) else {}
    findings = counts.get("findings_total")
    return AuditRow(
        id=r["id"],
        ts=r["ts"],
        tenant_id=r["tenant_id"],
        key_id=r["key_id"],
        key_name=key_names.get(r["key_id"] or ""),
        trace_id=r["trace_id"],
        route=r["route"],
        action=r["action"],
        layer=r["layer"],
        mode=r["mode"],
        policy=r["policy"],
        outcome=r["outcome"],
        status=r["status"],
        job_id=r["job_id"],
        findings=int(findings) if isinstance(findings, int | float) else None,
        methods=_int_map(counts.get("methods_used")),
    )


async def query_audit(
    *,
    tenants: list[str],
    since: datetime,
    until: datetime,
    action: str | None,
    layer: str | None,
    outcome: str | None,
    q: str | None,
    limit: int,
    cursor: tuple[datetime, int] | None,
    key_names: Mapping[str, str],
) -> tuple[list[AuditRow], str | None]:
    """One page of audit rows across ``tenants`` (newest first) and the next cursor."""
    outcomes = OUTCOME_GROUPS.get(outcome) if outcome else None
    needle = q.strip() if q and q.strip() else None
    key_ids = (
        sorted(k for k, n in key_names.items() if needle.lower() in n.lower()) if needle else []
    )
    params = (
        since,
        until,
        action,
        layer,
        outcomes,
        needle,
        like_escape(needle) if needle else None,
        key_ids,
        cursor[0] if cursor else None,
        cursor[1] if cursor else None,
        limit + 1,
    )
    rows: list[AuditRow] = []
    sql = _sql()
    for tenant in tenants:
        async with db.acquire(tenant) as conn:
            found = await conn.fetch(sql, *params, tenant, like_escape(action) if action else None)
        rows += [_row(r, key_names) for r in found]
    rows.sort(key=lambda r: (r.ts, r.id), reverse=True)
    page = rows[:limit]
    more = len(rows) > limit
    return page, (encode_cursor(page[-1].ts, page[-1].id) if more and page else None)


def _methods(methods: Mapping[str, int]) -> str:
    return ";".join(f"{k}={v}" for k, v in sorted(methods.items()))


async def stream_audit_csv(
    *,
    tenants: list[str],
    since: datetime,
    until: datetime,
    action: str | None,
    layer: str | None,
    outcome: str | None,
    q: str | None,
    key_names: Mapping[str, str],
    max_rows: int = 1_000_000,
) -> AsyncIterator[bytes]:
    """The filtered audit rows as CSV, a page of :data:`CSV_PAGE` rows at a time."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_HEADER)
    yield buffer.getvalue().encode("utf-8")
    cursor: tuple[datetime, int] | None = None
    sent = 0
    while sent < max_rows:
        page, nxt = await query_audit(
            tenants=tenants,
            since=since,
            until=until,
            action=action,
            layer=layer,
            outcome=outcome,
            q=q,
            limit=min(CSV_PAGE, max_rows - sent),
            cursor=cursor,
            key_names=key_names,
        )
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        for r in page:
            writer.writerow(
                [
                    csv_cell(c)
                    for c in (
                        r.ts.isoformat(),
                        r.tenant_id,
                        r.key_name,
                        r.key_id,
                        r.route,
                        r.action,
                        r.layer,
                        r.mode,
                        r.policy,
                        r.findings,
                        _methods(r.methods),
                        r.outcome,
                        r.status,
                        r.job_id,
                    )
                ]
            )
        if page:
            yield buffer.getvalue().encode("utf-8")
        sent += len(page)
        if nxt is None or not page:
            return
        cursor = (page[-1].ts, page[-1].id)
