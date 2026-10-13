"""``ScanStore``: SQL for the UI scanner's tables (030) — pages, fields, request counts, events.

Every method runs on a connection the caller obtained with ``acquire(tenant_id)`` (API) or
``acquire_queue(tenant_id)`` (browser-worker), so RLS scopes rows to the tenant; queries still
filter by ``job_id``. The connections come from :mod:`dps.db`, whose pools register a JSON codec
for ``json``/``jsonb``, so jsonb parameters are passed as Python objects (never pre-encoded text).

Everything written here is value-free (types, counts, shapes, redacted URLs and labels) or
synthetic (``fill_*``). Nothing here logs; errors carry codes and column names only.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

from dps.layers.connectors.evidence import EvidencePack
from dps.layers.scanner.models import (
    FieldRecord,
    PageRecord,
    RequestCount,
    ScanEvent,
    ScanFieldOut,
    ScanPageOut,
)

_SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_TABLES = ("dps_scan_event", "dps_scan_request", "dps_scan_field", "dps_scan_page")
_MAX_LIMIT = 1000

_UPSERT_PAGE = """
INSERT INTO {s}.dps_scan_page (tenant_id, job_id, page_no, url_redacted, title_redacted, depth,
  parent_page_no, status, status_reason, discovered_via, template, http_status, final_url_redacted,
  form_count, field_count, personal_field_count, critical_field_count, forms, artefacts,
  rendered_findings, risk_flags, validation, capture_status, timings)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18::jsonb,
        $19::jsonb, $20::jsonb, $21::jsonb, $22::jsonb, $23, $24::jsonb)
ON CONFLICT (job_id, page_no) DO UPDATE SET
  url_redacted = EXCLUDED.url_redacted, title_redacted = EXCLUDED.title_redacted,
  status = EXCLUDED.status, status_reason = EXCLUDED.status_reason,
  http_status = EXCLUDED.http_status, final_url_redacted = EXCLUDED.final_url_redacted,
  form_count = EXCLUDED.form_count, field_count = EXCLUDED.field_count,
  personal_field_count = EXCLUDED.personal_field_count,
  critical_field_count = EXCLUDED.critical_field_count, forms = EXCLUDED.forms,
  artefacts = EXCLUDED.artefacts, rendered_findings = EXCLUDED.rendered_findings,
  risk_flags = EXCLUDED.risk_flags, validation = EXCLUDED.validation,
  capture_status = EXCLUDED.capture_status, timings = EXCLUDED.timings, updated_at = now()
"""

_FIELD_COLUMNS = (
    "tenant_id",
    "job_id",
    "page_no",
    "form_index",
    "field_no",
    "selector",
    "tag",
    "input_type",
    "name_attr",
    "id_attr",
    "label",
    "autocomplete",
    "pattern",
    "maxlength",
    "required",
    "options_count",
    "entity_type",
    "sensitivity",
    "confidence",
    "detectors",
    "evidence",
    "fill_synthesize",
    "fill_obfuscate",
    "validation_messages",
    "label_source",
    "frame_index",
    "in_shadow",
    "placeholder_shape",
    "prefilled",
    "persona_attr",
    "obfuscate_method",
    "fill_format",
    "fill_status",
    "risk_codes",
)
_FIELD_JSONB = frozenset({"evidence", "validation_messages"})
_INSERT_FIELD = (
    "INSERT INTO {s}.dps_scan_field ("
    + ", ".join(_FIELD_COLUMNS)
    + ") VALUES ("
    + ", ".join(
        f"${i}::jsonb" if col in _FIELD_JSONB else f"${i}"
        for i, col in enumerate(_FIELD_COLUMNS, start=1)
    )
    + ")"
)

_ADD_REQUESTS = """
INSERT INTO {s}.dps_scan_request (tenant_id, job_id, page_no, method, resource_type, decision,
  reason, host, count)
SELECT $1, $2, r.page_no, r.method, r.resource_type, r.decision, r.reason, r.host, r.count
FROM jsonb_to_recordset($3::jsonb) AS r(page_no int, method text, resource_type text,
                                         decision text, reason text, host text, count int)
ON CONFLICT (job_id, (COALESCE(page_no, -1)), method, (COALESCE(resource_type, '')), decision,
             reason, host)
DO UPDATE SET count = {s}.dps_scan_request.count + EXCLUDED.count
"""

_INSERT_EVENT = """
INSERT INTO {s}.dps_scan_event (tenant_id, job_id, seq, ts, kind, method, result, page_no,
  target_redacted, note_code, counts)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
ON CONFLICT (job_id, seq) DO NOTHING
"""

_SUMMARY = """
SELECT
  (SELECT jsonb_object_agg(status, n) FROM (SELECT status, count(*) n FROM {s}.dps_scan_page
     WHERE job_id = $1 GROUP BY status) x) AS by_status,
  (SELECT count(*) FROM {s}.dps_scan_page WHERE job_id = $1) AS pages_discovered,
  (SELECT count(*) FROM {s}.dps_scan_page WHERE job_id = $1
     AND status IN ('done', 'redirected', 'failed')) AS pages_visited,
  (SELECT count(*) FROM {s}.dps_scan_page WHERE job_id = $1 AND status = 'queued') AS pages_queued,
  (SELECT count(*) FROM {s}.dps_scan_page WHERE job_id = $1
     AND status IN ('done', 'redirected')) AS classified,
  (SELECT count(*) FROM {s}.dps_scan_page WHERE job_id = $1
     AND artefacts ? 'filled_synthesize') AS filled,
  (SELECT count(*) FROM {s}.dps_scan_page WHERE job_id = $1
     AND capture_status = 'captured') AS captures,
  (SELECT coalesce(sum(form_count), 0) FROM {s}.dps_scan_page WHERE job_id = $1) AS forms,
  (SELECT coalesce(sum(field_count), 0) FROM {s}.dps_scan_page WHERE job_id = $1) AS fields,
  (SELECT coalesce(sum(personal_field_count), 0) FROM {s}.dps_scan_page
     WHERE job_id = $1) AS personal,
  (SELECT coalesce(sum(critical_field_count), 0) FROM {s}.dps_scan_page
     WHERE job_id = $1) AS critical,
  (SELECT jsonb_object_agg(method, n) FROM (SELECT method, sum(count) n FROM {s}.dps_scan_request
     WHERE job_id = $1 AND decision = 'blocked' GROUP BY method) y) AS blocked,
  (SELECT coalesce(sum(count), 0) FROM {s}.dps_scan_request WHERE job_id = $1
     AND decision = 'allowed' AND method NOT IN ('GET', 'HEAD')) AS non_get_allowed,
  (SELECT coalesce(sum(count), 0) FROM {s}.dps_scan_request WHERE job_id = $1
     AND decision = 'allowed' AND reason IN ('deny', 'not_allowed')) AS scope_paths_reached,
  (SELECT count(*) FROM {s}.dps_scan_event WHERE job_id = $1 AND kind = 'navigate') AS navigations
"""
_COUNT_KEYS = (
    "pages_discovered",
    "pages_visited",
    "pages_queued",
    "classified",
    "filled",
    "captures",
    "forms",
    "fields",
    "personal",
    "critical",
    "non_get_allowed",
    "scope_paths_reached",
    "navigations",
)

_EXPORTS = """
SELECT DISTINCT ON (kind) kind, id FROM {s}.dps_artefact
WHERE job_id = $1 AND kind IN ('export_json', 'export_csv')
ORDER BY kind, created_at DESC, id DESC
"""

_EXISTING_EVIDENCE = """
SELECT id, pack->>'outcome' AS outcome, html_artefact_id, pack->'target'->'exports' AS exports
FROM {s}.dps_evidence WHERE job_id = $1 ORDER BY created_at, id LIMIT 1
"""

_INSERT_EVIDENCE = """
INSERT INTO {s}.dps_evidence (id, tenant_id, job_id, pack, pack_sha256, pack_version,
  html_artefact_id, expires_at)
VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8)
RETURNING id
"""


@dataclass(frozen=True, slots=True)
class StoredEvidence:
    """A scan's stored evidence row: its id, the pack's outcome and the artefacts it references."""

    id: str
    outcome: str
    html_artefact_id: str | None
    exports: dict[str, str]

    @property
    def artefact_ids(self) -> frozenset[str]:
        """Every artefact the pack references (its HTML rendering and its exports)."""
        ids = set(self.exports.values())
        if self.html_artefact_id:
            ids.add(self.html_artefact_id)
        return frozenset(ids)


def _limit(limit: int) -> int:
    return max(1, min(int(limit), _MAX_LIMIT))


class ScanStore:
    """Persistence for one metadata schema's scan tables (see the module docstring)."""

    def __init__(self, schema: str) -> None:
        """``schema`` must match ``^[a-z_][a-z0-9_]{0,62}$``; it is quoted once here."""
        if not _SCHEMA_RE.fullmatch(schema):
            raise ValueError("schema must match ^[a-z_][a-z0-9_]{0,62}$")
        s = f'"{schema}"'
        self._s = s
        self._upsert_page = _UPSERT_PAGE.format(s=s)
        self._insert_field = _INSERT_FIELD.format(s=s)
        self._add_requests = _ADD_REQUESTS.format(s=s)
        self._insert_event = _INSERT_EVENT.format(s=s)
        self._summary = _SUMMARY.format(s=s)
        self._exports = _EXPORTS.format(s=s)
        self._insert_evidence = _INSERT_EVIDENCE.format(s=s)
        self._existing_evidence = _EXISTING_EVIDENCE.format(s=s)

    # --- writes --------------------------------------------------------------------------
    async def reset_scan(self, conn: asyncpg.Connection, job_id: str) -> None:
        """Delete the job's rows from the four scan tables (a requeued attempt starts clean)."""
        async with conn.transaction():
            for table in _TABLES:
                await conn.execute(f"DELETE FROM {self._s}.{table} WHERE job_id = $1", job_id)

    async def upsert_page(
        self, conn: asyncpg.Connection, tenant_id: str, job_id: str, page: PageRecord
    ) -> None:
        """Insert the page row, or update its outcome (discovery facts — depth, parent, template,
        how it was found — keep their first values)."""
        await conn.execute(
            self._upsert_page,
            tenant_id,
            job_id,
            page.page_no,
            page.url_redacted,
            page.title_redacted,
            page.depth,
            page.parent_page_no,
            page.status,
            page.status_reason,
            page.discovered_via,
            page.template,
            page.http_status,
            page.final_url_redacted,
            page.form_count,
            page.field_count,
            page.personal_field_count,
            page.critical_field_count,
            page.forms,
            page.artefacts,
            page.rendered_findings,
            [flag.model_dump(mode="json") for flag in page.risk_flags],
            page.validation,
            page.capture_status,
            page.timings,
        )

    async def replace_fields(
        self,
        conn: asyncpg.Connection,
        tenant_id: str,
        job_id: str,
        page_no: int,
        fields: Sequence[FieldRecord],
    ) -> None:
        """Replace the page's field rows (idempotent per page)."""
        if any(f.page_no != page_no for f in fields):
            raise ValueError("every field must belong to page_no")
        rows = []
        for f in fields:
            data = f.model_dump(mode="json")
            data["tenant_id"], data["job_id"] = tenant_id, job_id
            rows.append(tuple(data[col] for col in _FIELD_COLUMNS))
        async with conn.transaction():
            await conn.execute(
                f"DELETE FROM {self._s}.dps_scan_field WHERE job_id = $1 AND page_no = $2",
                job_id,
                page_no,
            )
            if rows:
                await conn.executemany(self._insert_field, rows)

    async def add_request_counts(
        self,
        conn: asyncpg.Connection,
        tenant_id: str,
        job_id: str,
        counts: Sequence[RequestCount],
    ) -> None:
        """Add the counts to the request log (rows with the same key accumulate)."""
        merged: dict[tuple[Any, ...], dict[str, Any]] = {}
        for c in counts:
            key = (c.page_no, c.method, c.resource_type, c.decision, c.reason, c.host)
            if key in merged:
                merged[key]["count"] += c.count
            else:
                merged[key] = c.model_dump(mode="json")
        if merged:
            await conn.execute(self._add_requests, tenant_id, job_id, list(merged.values()))

    async def append_events(
        self,
        conn: asyncpg.Connection,
        tenant_id: str,
        job_id: str,
        events: Sequence[ScanEvent],
    ) -> None:
        """Append navigation-log rows; a ``seq`` already stored is ignored (retries are safe)."""
        if not events:
            return
        await conn.executemany(
            self._insert_event,
            [
                (
                    tenant_id,
                    job_id,
                    e.seq,
                    e.ts,
                    e.kind,
                    e.method,
                    e.result,
                    e.page_no,
                    e.target,
                    e.note_code,
                    e.counts,
                )
                for e in events
            ],
        )

    async def insert_evidence(
        self,
        conn: asyncpg.Connection,
        tenant_id: str,
        job_id: str,
        pack: EvidencePack,
        *,
        pack_sha256: str,
        html_artefact_id: str | None,
        expires_at: datetime | None,
    ) -> str:
        """Insert the scan's evidence row (``id`` = ``pack.id``, minted by the caller because the
        pack embeds it) and return its id."""
        if pack.tenant_id != tenant_id or pack.job_id != job_id:
            raise ValueError("evidence pack belongs to another tenant or job")
        return str(
            await conn.fetchval(
                self._insert_evidence,
                pack.id,
                tenant_id,
                job_id,
                pack.model_dump(mode="json"),
                pack_sha256,
                pack.pack_version,
                html_artefact_id,
                expires_at,
            )
        )

    async def lock_leased_job(self, conn: asyncpg.Connection, job_id: str, worker_id: str) -> bool:
        """Lock the job row while ``worker_id`` still holds its lease (``FOR UPDATE``, so call it
        inside a transaction): the fence of a write that must happen once per job. ``False`` = the
        lease is gone (canceled or reaped)."""
        row = await conn.fetchval(
            f"SELECT 1 FROM {self._s}.dps_job WHERE id = $1 AND locked_by = $2 "
            "AND state = 'running' FOR UPDATE",
            job_id,
            worker_id,
        )
        return row is not None

    async def existing_evidence(
        self, conn: asyncpg.Connection, job_id: str
    ) -> StoredEvidence | None:
        """The pack an earlier finalize attempt of this job stored (packs are immutable), or
        ``None``."""
        row = await conn.fetchrow(self._existing_evidence, job_id)
        if row is None:
            return None
        exports = row["exports"] if isinstance(row["exports"], dict) else {}
        return StoredEvidence(
            id=str(row["id"]),
            outcome=str(row["outcome"] or ""),
            html_artefact_id=row["html_artefact_id"],
            exports={str(k): str(v) for k, v in exports.items() if isinstance(v, str)},
        )

    # --- reads ---------------------------------------------------------------------------
    async def list_pages(
        self, conn: asyncpg.Connection, job_id: str, *, after_page_no: int = 0, limit: int = 200
    ) -> list[ScanPageOut]:
        """Pages in ``page_no`` order after ``after_page_no`` (keyset paging)."""
        rows = await conn.fetch(
            f"SELECT * FROM {self._s}.dps_scan_page WHERE job_id = $1 AND page_no > $2 "
            "ORDER BY page_no LIMIT $3",
            job_id,
            after_page_no,
            _limit(limit),
        )
        return [ScanPageOut.model_validate(dict(r)) for r in rows]

    async def all_pages(self, conn: asyncpg.Connection, job_id: str) -> list[ScanPageOut]:
        """Every page of the job in ``page_no`` order (``list_pages`` pages are capped at
        ``_MAX_LIMIT``; this walks them all)."""
        out: list[ScanPageOut] = []
        after = 0
        while True:
            batch = await self.list_pages(conn, job_id, after_page_no=after, limit=_MAX_LIMIT)
            out += batch
            if len(batch) < _MAX_LIMIT:
                return out
            after = batch[-1].page_no

    async def get_page(
        self, conn: asyncpg.Connection, job_id: str, page_no: int
    ) -> ScanPageOut | None:
        """One page, or None."""
        row = await conn.fetchrow(
            f"SELECT * FROM {self._s}.dps_scan_page WHERE job_id = $1 AND page_no = $2",
            job_id,
            page_no,
        )
        return ScanPageOut.model_validate(dict(row)) if row else None

    async def list_fields(
        self, conn: asyncpg.Connection, job_id: str, page_no: int | None = None
    ) -> list[ScanFieldOut]:
        """The job's fields (one page's when ``page_no`` is given), in overlay order."""
        rows = await conn.fetch(
            f"SELECT * FROM {self._s}.dps_scan_field WHERE job_id = $1 "
            "AND ($2::int IS NULL OR page_no = $2) ORDER BY page_no, form_index, field_no",
            job_id,
            page_no,
        )
        return [ScanFieldOut.model_validate(dict(r)) for r in rows]

    async def list_requests(
        self, conn: asyncpg.Connection, job_id: str, page_no: int | None = None
    ) -> list[RequestCount]:
        """The request log (one page's when ``page_no`` is given)."""
        rows = await conn.fetch(
            f"SELECT page_no, method, resource_type, decision, reason, host, count "
            f"FROM {self._s}.dps_scan_request WHERE job_id = $1 "
            "AND ($2::int IS NULL OR page_no = $2) "
            "ORDER BY page_no NULLS FIRST, method, decision, reason, host, resource_type",
            job_id,
            page_no,
        )
        return [RequestCount.model_validate(dict(r)) for r in rows]

    async def list_events(
        self, conn: asyncpg.Connection, job_id: str, *, after_seq: int = 0, limit: int = 200
    ) -> list[ScanEvent]:
        """Navigation-log rows in ``seq`` order after ``after_seq`` (keyset paging)."""
        rows = await conn.fetch(
            f"SELECT seq, ts, kind, method, result, page_no, target_redacted AS target, "
            f"note_code, counts FROM {self._s}.dps_scan_event WHERE job_id = $1 AND seq > $2 "
            "ORDER BY seq LIMIT $3",
            job_id,
            after_seq,
            _limit(limit),
        )
        return [ScanEvent.model_validate(dict(r)) for r in rows]

    async def summary_counts(self, conn: asyncpg.Connection, job_id: str) -> dict[str, Any]:
        """The scan's counters: ``by_status`` and ``blocked`` (by method) maps, page/form/field
        totals, ``non_get_allowed`` (must stay 0), ``scope_paths_reached`` (requests to a denied or
        not-allowed path that left the browser: an HTTPS redirect hop, see ``guard``) and
        ``navigations``."""
        row = await conn.fetchrow(self._summary, job_id)
        out: dict[str, Any] = {
            "by_status": dict(row["by_status"] or {}),
            "blocked": {k: int(v) for k, v in (row["blocked"] or {}).items()},
        }
        for key in _COUNT_KEYS:
            out[key] = int(row[key])
        return out

    async def export_artefacts(self, conn: asyncpg.Connection, job_id: str) -> dict[str, str]:
        """``{"json": id, "csv": id}`` of the job's newest export artefacts (missing kinds left
        out)."""
        rows = await conn.fetch(self._exports, job_id)
        return {r["kind"].removeprefix("export_"): r["id"] for r in rows}

    async def registered_hosts(self, conn: asyncpg.Connection, tenant_id: str) -> list[str]:
        """The tenant's registered hosts (``[]`` when the tenant has no row)."""
        hosts = await conn.fetchval(
            f"SELECT registered_hosts FROM {self._s}.dps_tenant WHERE tenant_id = $1", tenant_id
        )
        return list(hosts or [])

    async def browser_workers_online(
        self, conn: asyncpg.Connection, *, heartbeat_seconds: int
    ) -> int:
        """Browser-workers seen within ``heartbeat_seconds`` (``dps_worker`` is global)."""
        return int(
            await conn.fetchval(
                f"SELECT count(*) FROM {self._s}.dps_worker WHERE role = 'browser-worker' "
                "AND last_seen > now() - make_interval(secs => $1)",
                float(heartbeat_seconds),
            )
        )
