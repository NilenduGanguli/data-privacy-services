"""Report roll-ups (M5, migration 041) and the queries behind the Overview and Reports pages.

:func:`refresh_rollups` recomputes one tenant's days from the audit log, the job and partition
tables and the evidence packs into ``dps_report_daily`` / ``dps_report_entity_daily`` (and marks
every day of the range in ``dps_report_refresh``, empty days too). It runs every statement in one
transaction under the tenant's GUC (RLS stays the gate) after ``pg_try_advisory_xact_lock`` — a
second concurrent refresh of the same tenant returns ``False`` instead of waiting. A refresh is
idempotent: the range is deleted first and recomputed from the sources, so a retried job lands in
its final state. The routes call :func:`ensure_fresh` for their window: days never refreshed, plus
today and yesterday when older than :data:`FRESH_SECONDS`.

Roll-up rows hold an action name, a mode, an outcome, an entity type and numbers; the summaries
built from them add job ids, object names (``schema.table``), policy names and tickets — the same
value-free fields the evidence packs carry. Never a value, a key, a ``reveal_map`` or a partition
key range.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlsplit

import asyncpg
from pydantic import BaseModel, Field

from dps import db, jobs
from dps.audit_query import layer_case, like_escape
from dps.config import Settings
from dps.engine.taxonomy import entity_info
from dps.observability import get_logger

logger = get_logger(__name__)

#: Today's and yesterday's roll-ups are recomputed when older than this.
FRESH_SECONDS = 60
#: ``SET LOCAL statement_timeout`` of the exact audit-row count (falls back to the API calls).
AUDIT_COUNT_TIMEOUT = "5s"
MAX_FAILED_SWEEPS = 100
_ENTITY = r"^[A-Z][A-Z0-9_]{0,63}$"
_COUNT = r"^[0-9]{1,18}$"
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

#: UI sweeps (S2 console r2 B1): a UI sweep is one scan's own verification. A scan that succeeded
#: passed it; a scan whose evidence pack records a verification error (``submission_detected``: its
#: request log held a non-GET request the guard allowed) failed it. A scan that failed before it
#: swept anything (a setup error such as ``secret_unavailable``, a crash: no pack, or a pack with no
#: error) and a canceled scan are no sweep — the scanner and job pages show them. Read from the job
#: and evidence tables (always fresh, one definition for the tile, the chart, the Failed sweeps
#: card and the Overview posture); a scan counts on its finish day (UTC), as the roll-ups do.
_UI_SWEEPS = """
SELECT j.id AS job_id, (j.finished_at AT TIME ZONE 'UTC')::date AS day,
       coalesce(jsonb_array_length(ev.errors), 0) > 0 AS failed, ev.errors
FROM __S__.dps_job j
LEFT JOIN LATERAL (
  SELECT CASE WHEN jsonb_typeof(e.pack->'errors') = 'array' THEN e.pack->'errors' END AS errors
  FROM __S__.dps_evidence e WHERE e.job_id = j.id AND e.tenant_id = j.tenant_id
  ORDER BY e.created_at DESC LIMIT 1) ev ON true
WHERE j.tenant_id = $1 AND j.kind = 'scan' AND j.finished_at >= $2
  AND (j.state = 'succeeded' OR coalesce(jsonb_array_length(ev.errors), 0) > 0)
ORDER BY j.finished_at DESC, j.id
"""

_STATEMENTS = r"""
DELETE FROM __S__.dps_report_daily WHERE tenant_id = $1 AND day BETWEEN $2 AND $3;
DELETE FROM __S__.dps_report_entity_daily WHERE tenant_id = $1 AND day BETWEEN $2 AND $3;
INSERT INTO __S__.dps_report_daily (tenant_id, day, layer, action, mode, outcome, calls, findings,
       values_transformed, verify_passed, verify_failed, llm_calls, llm_raw_values, llm_blocked)
SELECT a.tenant_id, (a.ts AT TIME ZONE 'UTC')::date, __LAYER__, a.action, coalesce(a.mode, '-'),
       a.outcome, count(*),
       coalesce(sum(CASE WHEN jsonb_typeof(a.counts->'findings_total') = 'number'
                         THEN (a.counts->>'findings_total')::numeric::bigint END), 0),
       coalesce(sum(m.n), 0),
       count(*) FILTER (WHERE a.counts->>'verification_passed' = 'true'),
       count(*) FILTER (WHERE a.counts->>'verification_passed' = 'false'),
       __AUDIT_NUM(llm_calls)__, __AUDIT_NUM(llm_raw_values)__, __AUDIT_NUM(llm_blocked)__
FROM __S__.dps_audit_log a
LEFT JOIN LATERAL (
  SELECT sum(e.value::bigint) AS n
  FROM jsonb_each_text(CASE WHEN jsonb_typeof(a.counts->'methods_used') = 'object'
                            THEN a.counts->'methods_used' ELSE '{}'::jsonb END) e
  WHERE e.key <> 'keep' AND e.value ~ '__COUNT__') m ON true
WHERE a.tenant_id = $1 AND __W(a.ts)__ AND __LAYER__ IN ('api', 'admin')
GROUP BY 1, 2, 3, 4, 5, 6;
INSERT INTO __S__.dps_report_entity_daily (tenant_id, day, layer, entity_type, findings)
SELECT a.tenant_id, (a.ts AT TIME ZONE 'UTC')::date, 'api', e.key, sum(e.value::bigint)
FROM __S__.dps_audit_log a,
     jsonb_each_text(CASE WHEN jsonb_typeof(a.counts->'findings_by_type') = 'object'
                          THEN a.counts->'findings_by_type' ELSE '{}'::jsonb END) e
WHERE a.tenant_id = $1 AND __W(a.ts)__ AND __LAYER__ = 'api'
  AND e.key ~ '__ENTITY__' AND e.value ~ '__COUNT__'
GROUP BY 1, 2, 4;
INSERT INTO __S__.dps_report_daily (tenant_id, day, layer, action, mode, outcome, calls,
       values_transformed, rows_written, pages, fields, blocked_requests, llm_calls,
       llm_raw_values, llm_blocked)
SELECT j.tenant_id, (j.finished_at AT TIME ZONE 'UTC')::date,
       CASE WHEN j.kind = 'scan' THEN 'ui' ELSE 'conn' END, 'job.' || j.kind, coalesce(j.mode, '-'),
       j.state, count(*), coalesce(sum(v.n), 0),
       coalesce(sum(CASE WHEN jsonb_typeof(j.progress->'rows_written') = 'number'
                         THEN (j.progress->>'rows_written')::numeric::bigint
                         ELSE pa.rows_written END), 0),
       coalesce(sum(CASE WHEN jsonb_typeof(j.progress->'pages') = 'number'
                         THEN (j.progress->>'pages')::numeric::bigint END), 0),
       coalesce(sum(CASE WHEN jsonb_typeof(j.progress->'fields') = 'number'
                         THEN (j.progress->>'fields')::numeric::bigint END), 0),
       coalesce(sum(CASE WHEN jsonb_typeof(j.progress->'blocked_requests') = 'number'
                         THEN (j.progress->>'blocked_requests')::numeric::bigint END), 0),
       __JOB_LLM(calls)__, __JOB_LLM(raw_values)__, __JOB_LLM(blocked)__
FROM __S__.dps_job j
LEFT JOIN LATERAL (
  SELECT sum(p.rows_written)::bigint AS rows_written,
         __S__.dps_jsonb_sum(p.values_by_method) AS values_by_method
  FROM __S__.dps_job_partition p WHERE p.job_id = j.id AND p.tenant_id = j.tenant_id) pa ON true
LEFT JOIN LATERAL (
  SELECT sum(e.value::bigint) AS n
  FROM jsonb_each_text(CASE WHEN jsonb_typeof(j.progress->'values_by_method') = 'object'
                            THEN j.progress->'values_by_method'
                            WHEN jsonb_typeof(pa.values_by_method) = 'object'
                            THEN pa.values_by_method ELSE '{}'::jsonb END) e
  WHERE e.key <> 'keep' AND e.key NOT LIKE '%\_resample' AND e.key NOT LIKE '%:%'
    AND e.value ~ '__COUNT__') v ON true
WHERE j.tenant_id = $1 AND j.kind IN ('run', 'discover', 'scan') AND j.finished_at IS NOT NULL
  AND __W(j.finished_at)__
GROUP BY 1, 2, 3, 4, 5, 6;
INSERT INTO __S__.dps_report_daily (tenant_id, day, layer, action, mode, outcome, calls,
       verify_passed, verify_failed, rows_written)
SELECT p.tenant_id, (p.finished_at AT TIME ZONE 'UTC')::date, 'conn', 'job.partition',
       coalesce(j.mode, '-'), p.state, count(*), count(*) FILTER (WHERE p.state = 'done'),
       count(*) FILTER (WHERE p.error_code = 'leakage'), coalesce(sum(p.rows_written), 0)
FROM __S__.dps_job_partition p JOIN __S__.dps_job j ON j.id = p.job_id
WHERE p.tenant_id = $1 AND p.state IN ('done', 'failed', 'dead') AND p.finished_at IS NOT NULL
  AND __W(p.finished_at)__
GROUP BY 1, 2, 3, 4, 5, 6;
INSERT INTO __S__.dps_report_entity_daily AS r (tenant_id, day, layer, entity_type, findings)
SELECT e.tenant_id, (e.created_at AT TIME ZONE 'UTC')::date, 'conn', c->>'entity_type',
       sum(CASE WHEN jsonb_typeof(c->'values') = 'number'
                THEN (c->>'values')::numeric::bigint ELSE 0 END)
FROM __S__.dps_evidence e,
     jsonb_array_elements(CASE WHEN jsonb_typeof(e.pack->'columns') = 'array'
                               THEN e.pack->'columns' ELSE '[]'::jsonb END) c
WHERE e.tenant_id = $1 AND __W(e.created_at)__ AND coalesce(e.pack->>'job_kind', 'run') <> 'scan'
  AND jsonb_typeof(c) = 'object' AND (c->>'entity_type') ~ '__ENTITY__'
GROUP BY 1, 2, 4
ON CONFLICT (tenant_id, day, layer, entity_type) DO UPDATE SET findings = r.findings + EXCLUDED.findings;
INSERT INTO __S__.dps_report_entity_daily AS r (tenant_id, day, layer, entity_type, findings)
SELECT e.tenant_id, (e.created_at AT TIME ZONE 'UTC')::date, 'ui', b.key, sum(b.value::bigint)
FROM __S__.dps_evidence e,
     jsonb_each_text(CASE WHEN jsonb_typeof(e.pack->'scan'->'by_entity') = 'object'
                          THEN e.pack->'scan'->'by_entity' ELSE '{}'::jsonb END) b
WHERE e.tenant_id = $1 AND __W(e.created_at)__ AND b.key ~ '__ENTITY__' AND b.value ~ '__COUNT__'
GROUP BY 1, 2, 4
ON CONFLICT (tenant_id, day, layer, entity_type) DO UPDATE SET findings = r.findings + EXCLUDED.findings;
INSERT INTO __S__.dps_report_refresh (tenant_id, day, refreshed_at)
SELECT $1, d::date, now() FROM generate_series($2::date, $3::date, interval '1 day') d
ON CONFLICT (tenant_id, day) DO UPDATE SET refreshed_at = now()
"""


def _window(column: str) -> str:
    return (
        f"{column} >= ($2::date)::timestamp AT TIME ZONE 'UTC' "
        f"AND {column} < (($3::date) + 1)::timestamp AT TIME ZONE 'UTC'"
    )


def _number(json_path: str) -> str:
    """``sum`` of a JSON number at ``json_path`` (non-numbers count 0), as bigint."""
    return (
        f"coalesce(sum(CASE WHEN jsonb_typeof({json_path}) = 'number' "
        f"THEN ({json_path})::text::numeric::bigint END), 0)"
    )


def _statements(schema: str) -> list[str]:
    """The roll-up statements with the schema, layer mapping and window inlined."""
    text = (
        _STATEMENTS.replace("__S__", schema)
        .replace("__LAYER__", layer_case("a.action"))
        .replace("__ENTITY__", _ENTITY)
        .replace("__COUNT__", _COUNT)
    )
    for column in ("a.ts", "j.finished_at", "p.finished_at", "e.created_at"):
        text = text.replace(f"__W({column})__", _window(column))
    for key in ("llm_calls", "llm_raw_values", "llm_blocked"):  # M5 Task 16B (044)
        text = text.replace(f"__AUDIT_NUM({key})__", _number(f"a.counts->'{key}'"))
    for key in ("calls", "raw_values", "blocked"):
        text = text.replace(
            f"__JOB_LLM({key})__", _number(f"j.progress->'metrics'->'llm'->'{key}'")
        )
    return [s.strip() for s in text.split(";\n") if s.strip()]


def rollup_lock_key(tenant_id: str) -> str:
    """The advisory-lock key of one tenant's roll-up refresh (``hashtext`` of it)."""
    return f"dps.rollup.{db.active_settings().pg_schema}.{tenant_id}"


def utc_today() -> date:
    """Today in UTC (the roll-ups' day)."""
    return datetime.now(UTC).date()


async def refresh_rollups(
    tenant_id: str, first: date, last: date, *, settings: Settings, worker: bool = False
) -> bool:
    """Recompute ``[first, last]`` for one tenant; ``False`` when another refresh holds the lock."""
    del settings  # the pools carry the settings; kept for the contract signature
    acquire = db.acquire_queue if worker else db.acquire
    statements = _statements(db.schema_ident())
    async with acquire(tenant_id) as conn, conn.transaction():
        locked = await conn.fetchval(
            "SELECT pg_try_advisory_xact_lock(hashtext($1))", rollup_lock_key(tenant_id)
        )
        if not locked:
            return False
        for statement in statements:
            await conn.execute(statement, tenant_id, first, last)
    return True


async def ensure_fresh(tenant_id: str, first: date, last: date, *, settings: Settings) -> None:
    """Refresh the days of ``[first, last]`` never refreshed, and today/yesterday when stale."""
    today = utc_today()
    async with db.acquire(tenant_id) as conn:
        rows = await conn.fetch(
            f"SELECT day, refreshed_at FROM {db.schema_ident()}.dps_report_refresh "
            "WHERE tenant_id = $1 AND day BETWEEN $2 AND $3",
            tenant_id,
            first,
            last,
        )
    seen = {r["day"]: r["refreshed_at"] for r in rows}
    stale_after = datetime.now(UTC) - timedelta(seconds=FRESH_SECONDS)
    due: list[date] = []
    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        recent = day >= today - timedelta(days=1)
        if day not in seen or (recent and seen[day] < stale_after):
            due.append(day)
    if due:
        await refresh_rollups(tenant_id, min(due), max(due), settings=settings)


# --- response models -----------------------------------------------------------------------------
class TrendPoint(BaseModel):
    """Values protected on one day, by mode."""

    day: date
    obfuscated: int = 0
    synthesized: int = 0


class EntityCount(BaseModel):
    """Findings of one entity type over the window."""

    entity_type: str
    findings: int
    sensitivity: str


class JobBrief(BaseModel):
    """One recent job (value-free note: "8 partitions", "fidelity 0.97", "awaiting review")."""

    id: str
    kind: str
    mode: str | None = None
    state: str
    source: str | None = None
    target: str | None = None
    progress_pct: int | None = None
    note: str | None = None


class SummaryOut(BaseModel):
    """The Overview page (mockup 01)."""

    tenant_id: str
    days: int
    generated_at: datetime
    tiles: dict[str, int | float]
    trend: list[TrendPoint]
    findings_by_type: list[EntityCount]
    layers: dict[str, dict[str, Any]]
    posture: dict[str, float]
    recent_jobs: list[JobBrief]


class SweepDay(BaseModel):
    """Leakage sweeps on one day in one layer."""

    day: date
    layer: str
    passed: int = 0
    failed: int = 0


class FailedSweep(BaseModel):
    """A sweep that failed: a job's partitions or an API call's verification (both failed closed),
    or a UI scan whose own verification failed (``where="scan"``, ``job_id`` the scan, ``error_code``
    the first error of its evidence pack; S2 console r2 B1)."""

    where: Literal["job", "api", "scan"]
    day: date
    job_id: str | None = None
    partitions: list[int] = Field(default_factory=list)
    route: str | None = None
    error_code: str
    paths: list[str] = Field(default_factory=list)


class ReportsJobsOut(BaseModel):
    """The Reports page's sweeps panel."""

    days: int
    sweeps: list[SweepDay]
    totals: dict[str, int]
    failed_sweeps: list[FailedSweep]


class EvidenceItem(BaseModel):
    """One evidence pack in the Reports list (summary keys only)."""

    id: str
    job_id: str
    job_kind: str
    outcome: str
    created_at: datetime
    pack_sha256: str
    layer: str
    summary: dict[str, Any]


class ApiRollup(BaseModel):
    """One day × action of Layer 1 calls."""

    day: date
    action: str
    calls: int
    findings: int
    values: int
    verify_failed: int


# --- queries -------------------------------------------------------------------------------------
def span(days: int) -> tuple[date, date]:
    """``(first, last)`` UTC days of a window of ``days`` ending today."""
    last = utc_today()
    return last - timedelta(days=days - 1), last


def _start_ts(first: date) -> datetime:
    return datetime(first.year, first.month, first.day, tzinfo=UTC)


async def _daily_rows(tenant_id: str, first: date, last: date) -> list[dict[str, Any]]:
    async with db.acquire(tenant_id) as conn:
        rows = await conn.fetch(
            f"SELECT * FROM {db.schema_ident()}.dps_report_daily "
            "WHERE tenant_id = $1 AND day BETWEEN $2 AND $3",
            tenant_id,
            first,
            last,
        )
    return [dict(r) for r in rows]


def _is_protecting(row: dict[str, Any]) -> bool:
    action = str(row["action"])
    if row["layer"] == "api":
        return action in ("obfuscate", "synthesize") or action.startswith("document.")
    return action in ("job.run", "job.scan")


def _mode_of(row: dict[str, Any]) -> str | None:
    text = f"{row['action']} {row['mode']}"
    if "synthesize" in text:
        return "synthesize"
    if "obfuscate" in text:
        return "obfuscate"
    return None


def _target(spec: dict[str, Any]) -> str | None:
    target = spec.get("target") if isinstance(spec.get("target"), dict) else None
    if target is None:
        return None
    mode = target.get("write_mode")
    obj = target.get("object")
    if mode == "files":
        return "files"
    if mode == "in_place":
        return "in place"
    if isinstance(obj, dict) and obj.get("name"):
        owner = obj.get("owner")
        return f"{owner}.{obj['name']}" if owner else str(obj["name"])
    return None


def scan_host(origin: Any) -> str | None:
    """``host[:port]`` of a scan's redacted origin (``http://demo-site:8410`` -> ``demo-site:8410``),
    the name the console gives a scan (mockups 01 and 09: "UI scan of <host>"); None without one."""
    if not isinstance(origin, str) or not origin:
        return None
    parts = urlsplit(origin)
    try:
        port = parts.port
    except ValueError:
        port = None
    host = parts.hostname
    if not host:
        return None
    host = f"[{host}]" if ":" in host else host
    return f"{host}:{port}" if port else host


async def _brief(tenant_id: str, job: jobs.Job) -> JobBrief:
    progress = job.progress
    total, done = progress.partitions_total, progress.partitions_done
    note: str | None = None
    if job.state.value in ("running", "waiting") and total:
        note = f"{total} partitions"
    elif job.mode == "synthesize" and isinstance(progress.metrics.get("fidelity"), dict):
        score = progress.metrics["fidelity"].get("score")
        if isinstance(score, int | float):
            note = f"fidelity {score:.2f}"
    elif job.kind.value == "discover" and progress.snapshot_ids:
        async with db.acquire(tenant_id) as conn:
            proposed = await conn.fetchval(
                f"SELECT count(*) FROM {db.schema_ident()}.dps_snapshot "
                "WHERE id = ANY($1::text[]) AND status = 'proposed'",
                progress.snapshot_ids,
            )
        note = "awaiting review" if proposed else None
    return JobBrief(
        id=job.id,
        kind=job.kind.value,
        mode=job.mode,
        state=job.state.value,
        # a scan has no source object: it is named by the host it scans (S2 console r1 B2)
        source=scan_host((job.spec or {}).get("url_origin"))
        if job.kind.value == "scan"
        else job.object_ref,
        target=_target(job.spec),
        progress_pct=round(100 * done / total) if total else None,
        note=note,
    )


async def _has_table(conn: asyncpg.Connection, name: str) -> bool:
    return await conn.fetchval("SELECT to_regclass($1)", f"{db.schema_ident()}.{name}") is not None


def _pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 1) if whole else 100.0


async def _ui_sweeps(conn: Any, tenant_id: str, start: datetime) -> list[dict[str, Any]]:
    """The UI sweeps finished since ``start`` (``_UI_SWEEPS``): ``job_id``, ``day``, ``failed`` and,
    for a failed one, ``error_code`` (its pack's first error code; value-free by construction)."""
    rows = await conn.fetch(_UI_SWEEPS.replace("__S__", db.schema_ident()), tenant_id, start)
    out: list[dict[str, Any]] = []
    for r in rows:
        errors = r["errors"] if isinstance(r["errors"], list) else []
        codes = [
            str(e["code"])
            for e in errors
            if isinstance(e, dict) and isinstance(e.get("code"), str) and _CODE.match(e["code"])
        ]
        out.append(
            {
                "job_id": r["job_id"],
                "day": r["day"],
                "failed": bool(r["failed"]),
                "error_code": codes[0] if codes else "verification_failed",
            }
        )
    return out


async def summary(tenant_id: str, days: int, settings: Settings) -> SummaryOut:
    """Tiles, trend, findings by type, layers, posture and recent jobs for the last ``days``."""
    first, last = span(days)
    prev_first = first - timedelta(days=days)
    await ensure_fresh(tenant_id, prev_first, last, settings=settings)
    rows = await _daily_rows(tenant_id, prev_first, last)
    current = [r for r in rows if r["day"] >= first]
    previous = [r for r in rows if r["day"] < first]
    s = db.schema_ident()
    start = _start_ts(first)
    async with db.acquire(tenant_id) as conn:
        entities = await conn.fetch(
            f"SELECT entity_type, sum(findings)::bigint AS n FROM {s}.dps_report_entity_daily "
            "WHERE tenant_id = $1 AND day BETWEEN $2 AND $3 GROUP BY entity_type "
            "ORDER BY n DESC, entity_type",
            tenant_id,
            first,
            last,
        )
        states = await conn.fetch(
            f"SELECT state, count(*) AS n FROM {s}.dps_job WHERE tenant_id = $1 "
            "AND kind IN ('run', 'scan', 'discover') GROUP BY state",
            tenant_id,
        )
        proposed = await conn.fetchval(
            f"SELECT count(*) FROM {s}.dps_snapshot WHERE tenant_id = $1 AND status = 'proposed'",
            tenant_id,
        )
        engines = await conn.fetch(
            f"SELECT c.engine, coalesce(sum(CASE WHEN jsonb_typeof(j.progress->'rows_written') = "
            "'number' THEN (j.progress->>'rows_written')::numeric::bigint "
            f"ELSE (SELECT sum(p.rows_written) FROM {s}.dps_job_partition p "
            "WHERE p.job_id = j.id AND p.tenant_id = j.tenant_id) END), 0)::bigint AS n "
            f"FROM {s}.dps_job j JOIN {s}.dps_connection c ON c.id = j.connection_id "
            "WHERE j.tenant_id = $1 AND j.kind = 'run' AND j.finished_at >= $2 GROUP BY c.engine",
            tenant_id,
            start,
        )
        groups = await conn.fetchval(
            f"SELECT count(*) FROM {s}.dps_job_group WHERE tenant_id = $1 AND closed_at IS NULL",
            tenant_id,
        )
        reviewed = await conn.fetchrow(
            "SELECT count(*) FILTER (WHERE sn.status IN ('accepted', 'superseded')) AS ok, "
            f"count(*) AS n FROM {s}.dps_job j LEFT JOIN {s}.dps_snapshot sn ON sn.id = j.snapshot_id "
            "WHERE j.tenant_id = $1 AND j.kind = 'run' AND j.created_at >= $2",
            tenant_id,
            start,
        )
        # M5 Task 15: run jobs that ran a tenant policy's active version (presets, inline
        # policies and policies the store does not know count as latest)
        latest = await conn.fetchrow(
            "SELECT count(*) FILTER (WHERE p.name IS NULL OR p.active_version IS NULL "
            "OR j.spec->'policy'->>'version' = p.active_version::text) AS ok, count(*) AS n "
            f"FROM {s}.dps_job j LEFT JOIN {s}.dps_policy p ON p.tenant_id = j.tenant_id "
            "AND jsonb_typeof(j.spec->'policy') = 'object' AND p.name = j.spec->'policy'->>'name' "
            "WHERE j.tenant_id = $1 AND j.kind = 'run' AND j.created_at >= $2",
            tenant_id,
            start,
        )
        risk_flags = personal_fields = 0
        if await _has_table(conn, "dps_scan_page"):  # M4's table (absent before the M4 merge)
            pages_row = await conn.fetchrow(
                "SELECT coalesce(sum(jsonb_array_length(risk_flags)), 0) AS flags, "
                "coalesce(sum(personal_field_count), 0) AS personal "
                f"FROM {s}.dps_scan_page WHERE tenant_id = $1 AND created_at >= $2",
                tenant_id,
                start,
            )
            risk_flags, personal_fields = int(pages_row["flags"]), int(pages_row["personal"])
        scan = await conn.fetchrow(
            f"SELECT id, progress, spec FROM {s}.dps_job WHERE tenant_id = $1 AND kind = 'scan' "
            "ORDER BY created_at DESC LIMIT 1",
            tenant_id,
        )
        ui_sweeps = await _ui_sweeps(conn, tenant_id, start)
    recent, _ = await jobs.list_jobs(tenant_id, limit=6)

    def protected(rs: list[dict[str, Any]]) -> int:
        return sum(int(r["values_transformed"]) for r in rs if _is_protecting(r))

    trend = {
        first + timedelta(days=i): TrendPoint(day=first + timedelta(days=i)) for i in range(days)
    }
    for r in current:
        mode = _mode_of(r) if _is_protecting(r) else None
        if mode == "obfuscate":
            trend[r["day"]].obfuscated += int(r["values_transformed"])
        elif mode == "synthesize":
            trend[r["day"]].synthesized += int(r["values_transformed"])
    by_state = {r["state"]: int(r["n"]) for r in states}
    job_rows = [r for r in current if r["action"] in ("job.run", "job.scan", "job.discover")]
    api = [r for r in current if r["layer"] == "api"]
    by_mode: dict[str, int] = {}
    for r in api:
        by_mode[str(r["action"])] = by_mode.get(str(r["action"]), 0) + int(r["calls"])
    scans = [r for r in current if r["action"] == "job.scan"]
    parts = [r for r in current if r["action"] == "job.partition"]
    # one meaning of a sweep on every surface: API batches, partitions and UI scans (``_UI_SWEEPS``)
    passed = sum(int(r["verify_passed"]) for r in api + parts)
    passed += sum(1 for u in ui_sweeps if not u["failed"])
    failed = sum(int(r["verify_failed"]) for r in api + parts)
    failed += sum(1 for u in ui_sweeps if u["failed"])
    last_scan: dict[str, Any] | None = None
    if scan is not None:
        progress = scan["progress"] if isinstance(scan["progress"], dict) else {}
        fill = (scan["spec"] or {}).get("fill") if isinstance(scan["spec"], dict) else None
        last_scan = {
            "id": scan["id"],
            "pages": progress.get("pages"),
            "fields": progress.get("fields"),
            "personas": fill.get("personas") if isinstance(fill, dict) else None,
        }
    posture = {
        "sweeps_passed_pct": _pct(passed, passed + failed),
        "key_held_by_caller_pct": 100.0,  # D2: DPS never stores a caller's key material
        "snapshots_reviewed_pct": _pct(int(reviewed["ok"]), int(reviewed["n"])),
        "policies_latest_pct": _pct(int(latest["ok"]), int(latest["n"])),
    }
    posture["score"] = round(sum(posture.values()) / len(posture), 1)
    findings = [
        EntityCount(
            entity_type=r["entity_type"],
            findings=int(r["n"]),
            sensitivity=entity_info(r["entity_type"]).sensitivity.value,
        )
        for r in entities
    ]
    tiles: dict[str, int | float] = {
        "values_protected": protected(current),
        "values_protected_prev": protected(previous),
        "findings": sum(e.findings for e in findings),
        "entity_types": len(findings),
        "jobs_running": by_state.get("running", 0) + by_state.get("waiting", 0),
        "jobs_completed": sum(int(r["calls"]) for r in job_rows if r["outcome"] == "succeeded"),
        "jobs_failed": sum(int(r["calls"]) for r in job_rows if r["outcome"] in ("failed", "dead")),
        "snapshots_awaiting_review": int(proposed),
        # per-tenant LLM exposure (Task 16B, migration 044): this tenant's window sums
        "raw_pii_sent_to_llm": sum(int(r.get("llm_raw_values") or 0) for r in current),
        "llm_calls": sum(int(r.get("llm_calls") or 0) for r in current),
        "llm_blocked": sum(int(r.get("llm_blocked") or 0) for r in current),
    }
    return SummaryOut(
        tenant_id=tenant_id,
        days=days,
        generated_at=datetime.now(UTC),
        tiles=tiles,
        trend=[trend[d] for d in sorted(trend)],
        findings_by_type=findings,
        layers={
            "api": {"requests": sum(int(r["calls"]) for r in api), "by_mode": by_mode},
            "conn": {
                "rows": sum(int(r["rows_written"]) for r in current if r["action"] == "job.run"),
                "by_engine": {str(r["engine"]): int(r["n"]) for r in engines},
                "groups": int(groups),
            },
            "ui": {
                "scans": sum(int(r["calls"]) for r in scans),
                "pages": sum(int(r["pages"]) for r in scans),
                "fields": sum(int(r["fields"]) for r in scans),
                "personal_fields": personal_fields,  # the scans' pages' personal fields (M4)
                "risk_flags": risk_flags,
                "submissions": 0,  # every scan's evidence asserts it (M4, D4)
                "last_scan": last_scan,
            },
        },
        posture=posture,
        recent_jobs=[await _brief(tenant_id, j) for j in recent],
    )


async def jobs_report(tenant_id: str, days: int, settings: Settings) -> ReportsJobsOut:
    """Sweeps per day and layer, totals and the sweeps that failed (UI sweeps: ``_UI_SWEEPS``)."""
    first, last = span(days)
    await ensure_fresh(tenant_id, first, last, settings=settings)
    rows = await _daily_rows(tenant_id, first, last)
    sweeps = {
        (first + timedelta(days=i), layer): SweepDay(day=first + timedelta(days=i), layer=layer)
        for i in range(days)
        for layer in ("api", "conn", "ui")
    }
    for r in rows:
        if r["layer"] == "api":
            slot = sweeps[(r["day"], "api")]
            slot.passed += int(r["verify_passed"])
            slot.failed += int(r["verify_failed"])
        elif r["action"] == "job.partition":
            slot = sweeps[(r["day"], "conn")]
            slot.passed += int(r["verify_passed"])
            slot.failed += int(r["verify_failed"])
    s = db.schema_ident()
    start = _start_ts(first)
    async with db.acquire(tenant_id) as conn:
        ui_sweeps = await _ui_sweeps(conn, tenant_id, start)
        packs = await conn.fetch(
            "SELECT coalesce(pack->>'job_kind', 'run') AS kind, count(*) AS n "
            f"FROM {s}.dps_evidence WHERE tenant_id = $1 AND created_at >= $2 GROUP BY 1",
            tenant_id,
            start,
        )
        leaked = await conn.fetch(
            "SELECT p.job_id, min(p.finished_at) AS at, "
            "array_agg(p.partition_no ORDER BY p.partition_no) AS parts, "
            "(SELECT e.pack->'errors' FROM "
            f"{s}.dps_evidence e WHERE e.job_id = p.job_id ORDER BY e.created_at DESC LIMIT 1) "
            f"AS errors FROM {s}.dps_job_partition p WHERE p.tenant_id = $1 "
            "AND p.error_code = 'leakage' AND p.finished_at >= $2 "
            "GROUP BY p.job_id ORDER BY at DESC LIMIT $3",
            tenant_id,
            start,
            MAX_FAILED_SWEEPS,
        )
        api_failed = await conn.fetch(
            "SELECT (ts AT TIME ZONE 'UTC')::date AS day, route FROM "
            f"{s}.dps_audit_log WHERE tenant_id = $1 AND ts >= $2 "
            "AND outcome = 'verification_failed' GROUP BY 1, 2 ORDER BY 1 DESC, 2 LIMIT $3",
            tenant_id,
            start,
            MAX_FAILED_SWEEPS,
        )
        try:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL statement_timeout = '{AUDIT_COUNT_TIMEOUT}'")
                audit_rows = int(
                    await conn.fetchval(
                        f"SELECT count(*) FROM {s}.dps_audit_log WHERE tenant_id = $1 AND ts >= $2",
                        tenant_id,
                        start,
                    )
                )
        except asyncpg.QueryCanceledError:
            audit_rows = sum(int(r["calls"]) for r in rows if r["layer"] in ("api", "admin"))
    by_kind = {str(r["kind"]): int(r["n"]) for r in packs}
    failed_sweeps = [
        FailedSweep(
            where="job",
            day=r["at"].astimezone(UTC).date(),
            job_id=r["job_id"],
            partitions=list(r["parts"]),
            error_code="leakage",
            paths=sorted(
                {
                    str(e["where"])
                    for e in (r["errors"] if isinstance(r["errors"], list) else [])
                    if isinstance(e, dict) and isinstance(e.get("where"), str)
                }
            ),
        )
        for r in leaked
    ] + [
        FailedSweep(where="api", day=r["day"], route=r["route"], error_code="verification_failed")
        for r in api_failed
    ]
    for u in ui_sweeps:
        ui_slot = sweeps.get((u["day"], "ui"))
        if ui_slot is None:
            continue  # finished after the window's last day (not reachable: it ends today)
        if u["failed"]:
            ui_slot.failed += 1
        else:
            ui_slot.passed += 1
    failed_sweeps += [
        FailedSweep(where="scan", day=u["day"], job_id=u["job_id"], error_code=u["error_code"])
        for u in ui_sweeps
        if u["failed"]
    ][:MAX_FAILED_SWEEPS]
    values = list(sweeps.values())
    totals = {
        "packs": sum(by_kind.values()),
        "run_packs": sum(n for k, n in by_kind.items() if k != "scan"),
        "scan_packs": by_kind.get("scan", 0),
        "api_rollups": len({(r["day"], r["action"]) for r in rows if r["layer"] == "api"}),
        "sweeps_passed": sum(v.passed for v in values),
        "sweeps_failed": sum(v.failed for v in values),
        "audit_rows": audit_rows,
    }
    return ReportsJobsOut(
        days=days,
        sweeps=sorted(values, key=lambda v: (v.day, v.layer)),
        totals=totals,
        failed_sweeps=failed_sweeps,
    )


def _evidence_summary(pack: dict[str, Any]) -> dict[str, Any]:
    """The listed keys only — never ``reveal_map``, ``partitions`` or ``columns``."""

    def sub(key: str) -> dict[str, Any]:
        value = pack.get(key)
        return value if isinstance(value, dict) else {}

    target, verification, counts = sub("target"), sub("verification"), sub("counts")
    scan = sub("scan")
    return {
        "mode": pack.get("mode"),
        "policy": sub("policy").get("name"),
        "policy_version": sub("policy").get("version"),
        "ticket": sub("purpose").get("ticket"),
        # a scan pack names its origin, not an object (S2 console r1 B2)
        "source": sub("source").get("object") or scan_host(sub("source").get("origin")),
        "target": target.get("object")
        or (target["files"].get("prefix") if isinstance(target.get("files"), dict) else None),
        "write_mode": target.get("write_mode"),
        "rows_written": counts.get("rows_written"),
        "verification_passed": verification.get("passed"),
        # main's post-write gate and M5's D-A review (WP-I): a run that needs a reviewer
        "review_required": verification.get("review_required") is True,
        "verification_status": verification.get("status"),
        "hits": verification.get("hits"),
        "fidelity_score": sub("fidelity").get("score"),
        "exact_row_matches": sub("privacy").get("exact_row_matches"),
        "pages": scan.get("pages"),
        "forms": scan.get("forms"),
        "personas": scan.get("personas"),
        "submissions": scan.get("submissions"),
    }


_OUTCOMES = {"ok": ["succeeded", "dry_run"], "failed": ["failed", "canceled"]}


async def evidence_list(
    tenant_id: str,
    *,
    days: int,
    layer: str | None,
    outcome: str | None,
    q: str | None,
    limit: int,
    cursor: str | None,
    include_scans: bool,
) -> tuple[list[EvidenceItem], str | None]:
    """Evidence packs of the window, newest first, keyset-paged on ``(created_at, id)``.

    Raises:
        ValueError: a malformed cursor (``dps.jobs.decode_cursor``'s message).
    """
    first, _ = span(days)
    params: list[Any] = [tenant_id, _start_ts(first)]
    where = ["e.tenant_id = $1", "e.created_at >= $2"]
    kind = "coalesce(e.pack->>'job_kind', j.kind)"
    if not include_scans:
        where.append(f"{kind} <> 'scan'")
    if layer == "ui":
        where.append(f"{kind} = 'scan'")
    elif layer is not None:
        where.append(f"{kind} <> 'scan'")
    if outcome in _OUTCOMES:
        params.append(_OUTCOMES[outcome])
        where.append(f"e.pack->>'outcome' = ANY(${len(params)}::text[])")
    if q and q.strip():
        needle = q.strip().lower()
        params += [needle, like_escape(needle)]
        n = len(params) - 1
        where.append(
            f"(lower(e.job_id) = ${n} OR lower(e.pack->'purpose'->>'ticket') = ${n} "
            f"OR lower(e.pack->'policy'->>'name') = ${n} "
            f"OR e.pack->'source'->>'object' ILIKE '%' || ${n + 1} || '%' ESCAPE '\\')"
        )
    if cursor:
        ts, eid = jobs.decode_cursor(cursor)
        params += [ts, eid]
        where.append(f"(e.created_at, e.id) < (${len(params) - 1}::timestamptz, ${len(params)})")
    page = max(1, min(200, limit))
    async with db.acquire(tenant_id) as conn:
        rows = await conn.fetch(
            f"SELECT e.id, e.job_id, {kind} AS job_kind, e.pack, e.pack_sha256, e.created_at "
            f"FROM {db.schema_ident()}.dps_evidence e JOIN {db.schema_ident()}.dps_job j "
            f"ON j.id = e.job_id WHERE {' AND '.join(where)} "
            f"ORDER BY e.created_at DESC, e.id DESC LIMIT {page + 1}",
            *params,
        )
    items = [
        EvidenceItem(
            id=r["id"],
            job_id=r["job_id"],
            job_kind=r["job_kind"],
            outcome=str((r["pack"] or {}).get("outcome", "")),
            created_at=r["created_at"],
            pack_sha256=r["pack_sha256"],
            layer="ui" if r["job_kind"] == "scan" else "conn",
            summary=_evidence_summary(r["pack"] if isinstance(r["pack"], dict) else {}),
        )
        for r in rows[:page]
    ]
    nxt = (
        jobs.encode_cursor(items[-1].created_at, items[-1].id)
        if len(rows) > page and items
        else None
    )
    return items, nxt


async def api_rollups(tenant_id: str, days: int) -> list[ApiRollup]:
    """Layer 1 calls per day and action (newest day first)."""
    first, last = span(days)
    async with db.acquire(tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT day, action, sum(calls)::bigint AS calls, sum(findings)::bigint AS findings, "
            "sum(values_transformed)::bigint AS vals, sum(verify_failed)::bigint AS vf "
            f"FROM {db.schema_ident()}.dps_report_daily WHERE tenant_id = $1 AND layer = 'api' "
            "AND day BETWEEN $2 AND $3 GROUP BY day, action ORDER BY day DESC, action",
            tenant_id,
            first,
            last,
        )
    return [
        ApiRollup(
            day=r["day"],
            action=r["action"],
            calls=r["calls"],
            findings=r["findings"],
            values=r["vals"],
            verify_failed=r["vf"],
        )
        for r in rows
    ]


class RollupNotFound(Exception):
    """No API roll-up for that day and action."""


async def api_rollup_doc(tenant_id: str, day: date, action: str) -> dict[str, Any]:
    """The downloadable document of one day × action (``sha256`` over its canonical JSON)."""
    s = db.schema_ident()
    async with db.acquire(tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT outcome, calls, findings, values_transformed, verify_passed, verify_failed "
            f"FROM {s}.dps_report_daily WHERE tenant_id = $1 AND layer = 'api' AND day = $2 "
            "AND action = $3",
            tenant_id,
            day,
            action,
        )
        entities = await conn.fetch(
            f"SELECT entity_type, findings FROM {s}.dps_report_entity_daily WHERE tenant_id = $1 "
            "AND layer = 'api' AND day = $2 ORDER BY entity_type",
            tenant_id,
            day,
        )
    if not rows:
        raise RollupNotFound()
    by_outcome: dict[str, int] = {}
    for r in rows:
        by_outcome[r["outcome"]] = by_outcome.get(r["outcome"], 0) + int(r["calls"])
    doc: dict[str, Any] = {
        "day": day.isoformat(),
        "action": action,
        "calls": sum(int(r["calls"]) for r in rows),
        "findings": sum(int(r["findings"]) for r in rows),
        "values": sum(int(r["values_transformed"]) for r in rows),
        "verify_passed": sum(int(r["verify_passed"]) for r in rows),
        "verify_failed": sum(int(r["verify_failed"]) for r in rows),
        "by_outcome": dict(sorted(by_outcome.items())),
        "entities": {r["entity_type"]: int(r["findings"]) for r in entities},
    }
    canonical = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("utf-8")
    doc["sha256"] = hashlib.sha256(canonical).hexdigest()
    doc["generated_at"] = datetime.now(UTC).isoformat()
    return doc
