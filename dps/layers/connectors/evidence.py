"""Evidence packs: build, hash, render, store (contract §7; scans reuse it in M4).

A pack holds counts, hashes, column names/paths, entity types, method names and their
(value-free) parameters, connection ids and names, key ids, seed fingerprints and the selection
**templates** — never a value, a key, a seed, a selection literal or a partition key position
("a key position is a customer identifier"). ``reveal_map`` lists every reversible column with
what reveal needs (``{path, entity_type, method, params, anchor_path, reveal, text_column}``);
``reveal`` is false for ``date_shift`` when the row anchor cannot be recomputed from the output
(the anchor column transformed one-way, or an append whose keys were replaced) — contract §6.2.

**Synthesize packs** (M5, contract §7.1/§7.2) carry the value-free figures ``prepare`` measured on
the deterministic preview (``progress.metrics``): ``fidelity``, ``privacy`` (plus the redraws the
partitions made, ``exact_row_resamples_run``), ``utility`` and ``tabular`` (engine, modelled and
excluded columns, constraints, fit/holdout/preview sizes). A synthesize run without tabular
synthesis says so in ``privacy`` (``measured: false``): its non-sensitive columns were copied from
the source, which is exactly what a reader of the pack must know.

``pack_sha256 = sha256(canonical JSON, sort_keys, compact separators)``; ``config_hash =
sha256(policy hash + snapshot hash + sha256(canonical redacted spec))``. The HTML rendering is a
self-contained page (every interpolation escaped, inline CSS with a print stylesheet, no
scripts). ``dps_evidence`` is append-only except its artefact ids (trigger, migration 006).
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from dps import db
from dps.connections import Connection
from dps.ids import PREFIX_EVIDENCE, new_id
from dps.jobs import JobInternal, PartitionView, decode_cursor, encode_cursor
from dps.jobs_models import ClassificationSnapshot, Purpose
from dps.layers.connectors.base import ObjectRef
from dps.layers.connectors.targets import RunPlan
from dps.storage import AcquireFn, ArtefactService

LABEL_OBFUSCATE = "pseudonymised — still personal data"
LABEL_SYNTHESIZE = "synthetic — measured, not claimed anonymous"
PACK_VERSION = "dps.evidence.v1"
Outcome = Literal["succeeded", "failed", "canceled", "dry_run"]
_HIDDEN = ConfigDict(hide_input_in_errors=True)
_MIN_LIMIT, _MAX_LIMIT = 1, 200
_REPLACED_KEYS = frozenset({"generate", "offset"})
#: Outcomes whose verification can pass: a failed or canceled run's output is incomplete.
FINISHED_OUTCOMES = frozenset({"succeeded", "dry_run"})


class EvidencePack(BaseModel):
    """Contract §7.1, plus the required ``label`` and the run ``mode`` (§7.2)."""

    model_config = _HIDDEN
    pack_version: Literal["dps.evidence.v1"] = "dps.evidence.v1"
    id: str
    tenant_id: str
    job_id: str
    job_kind: str
    group_id: str | None = None
    outcome: Outcome
    label: str
    mode: str | None = None
    created_at: datetime
    engine_version: str
    image_digest: str | None = None
    purpose: Purpose | None = None
    requested_by: str | None = None
    source: dict[str, Any]
    target: dict[str, Any]
    policy: dict[str, str]
    snapshot: dict[str, Any]
    consistency: dict[str, Any]
    columns: list[dict[str, Any]]
    reveal_map: list[dict[str, Any]]
    counts: dict[str, int]
    verification: dict[str, Any]
    fidelity: dict[str, Any] | None = None
    privacy: dict[str, Any] | None = None
    utility: dict[str, Any] | None = None
    tabular: dict[str, Any] | None = None
    scan: dict[str, Any] | None = None
    timings: dict[str, Any]
    partitions: list[dict[str, Any]]
    errors: list[dict[str, str]]
    config_hash: str


@dataclass
class EvidenceInputs:
    """Everything ``finalize`` knows about a finished run (all value-free).

    ``spec`` is the **redacted** spec of the job row (selection templates only), never the
    unsealed one.
    """

    job: JobInternal
    spec: dict[str, Any]
    plan: RunPlan | None
    snapshot: ClassificationSnapshot | None
    source_conn: Connection
    target_conn: Connection | None
    partitions: list[PartitionView]
    outcome: Outcome
    server_version: str | None
    prepare: dict[str, Any]
    files: dict[str, Any] | None
    verification: dict[str, Any]
    key_custody: dict[str, Any]
    errors: list[dict[str, str]]
    engine_version: str
    image_digest: str | None
    policy_version: str | None = None  # M5: the resolved policy's version, when known


class EvidenceRecord(BaseModel):
    """A stored pack with its hash and rendering artefacts."""

    model_config = _HIDDEN
    id: str
    job_id: str
    pack: EvidencePack
    pack_sha256: str
    html_artefact_id: str | None = None
    pdf_artefact_id: str | None = None
    json_artefact_id: str | None = None
    created_at: datetime


class EvidenceSummary(BaseModel):
    """One row of the evidence list (Reports, mockup 09)."""

    model_config = _HIDDEN
    id: str
    job_id: str
    created_at: datetime
    outcome: str
    mode: str | None = None
    label: str
    source_object: str | None = None
    target: str | None = None
    write_mode: str | None = None
    policy: str | None = None
    ticket: str | None = None
    passed: bool
    #: The post-write sample found personal data in a column the snapshot called not personal.
    review_required: bool = False
    hits: int
    rows_written: int
    html_artefact_id: str | None = None
    #: The first error of a failed or canceled run (a code, e.g. ``length_overflow``).
    error_code: str | None = None


def _canon(doc: Any) -> bytes:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def canonical_json(pack: EvidencePack) -> bytes:
    """The pack as canonical JSON (sorted keys, compact separators, UTF-8)."""
    return _canon(pack.model_dump(mode="json"))


def pack_sha256(pack: EvidencePack) -> str:
    """sha256 of :func:`canonical_json`."""
    return hashlib.sha256(canonical_json(pack)).hexdigest()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


#: Counter keys of tabular redraws (``<engine>_resample``, M5): counted, never a method.
RESAMPLE_KEYS = frozenset({"copula_resample", "mostly_resample"})


def _split_counters(
    counters: dict[str, int],
) -> tuple[dict[str, int], dict[str, int], dict[str, int], int]:
    """``(methods, fallbacks, per column, verification hits)`` from a counter map (tabular
    redraw counters are left out; :func:`_resamples` sums them)."""
    methods: dict[str, int] = {}
    fallbacks: dict[str, int] = {}
    columns: dict[str, int] = {}
    hits = 0
    for key, value in counters.items():
        if key.startswith("fallback:"):
            # counters stored before verify r2 issue 3 could read fallback:fallback:<reason>
            reason = re.sub(r"^(?:fallback:)+", "", key)
            fallbacks[reason] = fallbacks.get(reason, 0) + int(value)
        elif key.startswith("col:"):
            columns[key.removeprefix("col:")] = int(value)
        elif key == "verify:hits":
            hits = int(value)
        elif ":" not in key and key not in RESAMPLE_KEYS:
            methods[key] = int(value)
    return methods, fallbacks, columns, hits


def _resamples(counters: dict[str, int]) -> int:
    return sum(int(v) for k, v in counters.items() if k in RESAMPLE_KEYS)


def _synthetic_blocks(
    inputs: EvidenceInputs,
) -> tuple[
    dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None
]:
    """``(fidelity, privacy, utility, tabular)`` of a synthesize pack (``None`` otherwise)."""
    if inputs.job.mode != "synthesize":
        return None, None, None, None
    options = inputs.spec.get("options") or {}
    engine = options.get("tabular") or "none"
    if engine == "none":
        unmeasured: dict[str, Any] = {
            "engine": "none",
            "measured": False,
            "reason": "options.tabular=none: non-sensitive columns were copied from the source",
        }
        return None, unmeasured, None, None
    metrics = inputs.job.progress.metrics or {}
    fidelity = metrics.get("fidelity")
    measured = metrics.get("privacy")
    privacy: dict[str, Any]
    if measured is not None:
        privacy = {
            **measured,
            "exact_row_resamples_run": _resamples(inputs.job.progress.values_by_method),
        }
    else:
        privacy = {
            "engine": engine,
            "measured": False,
            "reason": "prepare stopped before the synthetic preview was measured",
        }
    return fidelity, privacy, metrics.get("utility"), metrics.get("tabular")


def _strategy_kind(plan: RunPlan) -> str | None:
    strategy = plan.target.pk_strategy
    return strategy.kind if strategy is not None else None


def anchor_recoverable(plan: RunPlan) -> bool:
    """True when a reader of the output can recompute each row's anchor (contract §6.2)."""
    names = [plan.anchor[0]] if plan.anchor else list(plan.key)
    replaced = plan.target.write_mode == "append" and _strategy_kind(plan) in _REPLACED_KEYS
    planned = {p.name: p for p in plan.columns}
    for name in names:
        if replaced and name in plan.key:
            return False
        column = planned.get(name)
        if column is not None and not column.reversible:
            return False
    return True


def _anchor_path(plan: RunPlan, snapshot: ClassificationSnapshot | None) -> str | None:
    if not plan.anchor:
        return None
    name = plan.anchor[0]
    if snapshot is not None:
        for c in snapshot.columns:
            if c.name == name:
                return c.path
    return f"$.{name}"


def _object_text(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    try:
        return ObjectRef.model_validate(value).fqn()
    except ValueError:
        return None


def _source(inputs: EvidenceInputs) -> dict[str, Any]:
    src = inputs.spec.get("source") or {}
    sel = src.get("selection") or {}
    obj = inputs.plan.source_object if inputs.plan else inputs.job.object_ref
    return {
        "engine": inputs.source_conn.engine,
        "connection_id": inputs.source_conn.id,
        "connection_name": inputs.source_conn.name,
        "object": obj or _object_text(src.get("object")),
        "selection": {
            "where_template": sel.get("where"),
            "query_template": sel.get("query"),
            "mongo_filter_template": sel.get("mongo_filter"),
            "binds": len(sel.get("params") or {}),
            "columns": sel.get("columns"),
            "max_rows": sel.get("max_rows"),
            "sample": sel.get("sample"),
        },
        "server_version": inputs.server_version,
    }


def _target(inputs: EvidenceInputs, source_object: str | None) -> dict[str, Any]:
    t = inputs.spec.get("target") or {}
    conn = inputs.target_conn or inputs.source_conn
    mode = t.get("write_mode")
    resolved = inputs.plan.target if inputs.plan else None
    if resolved is not None and resolved.object is not None:
        obj: str | None = resolved.object.fqn()
    elif mode in ("in_place", "append") and t.get("object") is None:
        obj = source_object
    else:
        obj = _object_text(t.get("object"))
    files: dict[str, Any] | None = None
    if mode == "files":
        spec_files = t.get("files") or {}
        files = {
            "format": spec_files.get("format"),
            "compression": spec_files.get("compression"),
            "prefix": spec_files.get("prefix"),
            "objects": 0,
            "rows": 0,
            "manifest_artefact_id": None,
            **(inputs.files or {}),
        }
    return {
        "engine": conn.engine,
        "connection_id": conn.id,
        "connection_name": conn.name,
        "object": None if mode == "files" else obj,
        "write_mode": mode,
        "table_load": t.get("table_load") if mode == "table" else None,
        "pk_strategy": t.get("pk_strategy"),
        "created": bool(inputs.prepare.get("created")),
        "truncated": bool(inputs.prepare.get("truncated")),
        "collisions": int(inputs.prepare.get("collisions") or 0),
        "existing_rows": inputs.prepare.get("existing_rows"),
        "confirm_write": bool(t.get("confirm_write")),
        "expected_rows": t.get("expected_rows"),
        "files": files,
    }


def build_pack(inputs: EvidenceInputs) -> EvidencePack:
    """Assemble the contract §7.1 pack from a finished run (module docstring: content rules)."""
    job, spec, plan, snapshot = inputs.job, inputs.spec, inputs.plan, inputs.snapshot
    now = datetime.now(UTC)
    methods, fallbacks, per_column, hits = _split_counters(job.progress.values_by_method)
    source = _source(inputs)
    consistency_spec = spec.get("consistency") or {}
    policy = {
        "name": plan.policy_name if plan else (snapshot.policy_name if snapshot else ""),
        "hash": plan.policy_hash if plan else (snapshot.policy_hash if snapshot else ""),
    }
    if inputs.policy_version is not None:
        policy["version"] = inputs.policy_version
    reviewed = {c.path: c for c in snapshot.columns} if snapshot is not None else {}
    fidelity, privacy, utility, tabular = _synthetic_blocks(inputs)
    snapshot_hash = plan.snapshot_hash if plan else (snapshot.content_hash if snapshot else "")
    columns: list[dict[str, Any]] = []
    reveal_map: list[dict[str, Any]] = []
    if plan is not None:
        recoverable = anchor_recoverable(plan)
        anchor_path = _anchor_path(plan, snapshot)
        for p in plan.columns:
            review = reviewed.get(p.path)
            columns.append(
                {
                    "path": p.path,
                    "entity_type": p.entity_type,
                    "data_type": review.data_type if review else None,
                    "confidence": review.confidence if review else None,
                    "override": review.override if review else False,
                    "method": p.method.method,
                    "params": p.method.params if p.reversible else {},
                    "reversible": p.reversible,
                    "text_column": p.text_column,
                    "values": per_column.get(p.name),
                    "fallbacks": None,
                }
            )
            if job.mode == "obfuscate" and p.reversible:
                reveal_map.append(
                    {
                        "path": p.path,
                        "entity_type": p.entity_type,
                        "method": p.method.method,
                        "params": p.method.params,
                        "anchor_path": anchor_path,
                        "reveal": recoverable or p.method.method != "date_shift",
                        "text_column": p.text_column,
                    }
                )
        for k in plan.pseudonyms:  # D-A: identifiers replaced by keyed pseudonyms, never copied
            review = reviewed.get(k.path)
            columns.append(
                {
                    "path": k.path,
                    "entity_type": k.entity_type,
                    "data_type": review.data_type if review else None,
                    "confidence": review.confidence if review else None,
                    "override": review.override if review else False,
                    "method": "key_pseudonym",
                    "params": {},
                    "reversible": False,
                    "text_column": False,
                    "values": per_column.get(k.name),
                    "fallbacks": None,
                }
            )
    progress = job.progress
    rows_written = int(progress.rows_written)
    started = job.started_at or job.created_at
    duration = max(0.0, (now - started).total_seconds())
    verification = {
        "mode": (spec.get("options") or {}).get("verify", "strict"),
        **inputs.verification,
    }
    verification.setdefault("hits", hits)
    if inputs.outcome not in FINISHED_OUTCOMES and verification.get("passed"):
        # N3: a run that did not finish is never "passed" (its output is incomplete, often empty)
        verification["passed"] = False
        verification["status"] = "incomplete"
    verification["fallbacks"] = fallbacks
    purpose = spec.get("purpose") or job.purpose or None
    return EvidencePack(
        id=new_id(PREFIX_EVIDENCE),
        tenant_id=job.tenant_id,
        job_id=job.id,
        job_kind=str(job.kind),
        group_id=job.group_id,
        outcome=inputs.outcome,
        label=LABEL_SYNTHESIZE if job.mode == "synthesize" else LABEL_OBFUSCATE,
        mode=job.mode,
        created_at=now,
        engine_version=inputs.engine_version,
        image_digest=inputs.image_digest,
        purpose=Purpose.model_validate(purpose) if purpose else None,
        requested_by=job.created_by,
        source=source,
        target=_target(inputs, source["object"]),
        policy=policy,
        snapshot={
            "id": snapshot.id if snapshot else (plan.snapshot_id if plan else job.snapshot_id),
            "content_hash": snapshot_hash or None,
            "accepted_by": snapshot.accepted_by if snapshot else None,
            "accepted_at": snapshot.accepted_at.isoformat()
            if snapshot and snapshot.accepted_at
            else None,
            "overrides": len(snapshot.overrides) if snapshot else 0,
        }
        if snapshot or plan
        else {
            "id": None,
            "content_hash": None,
            "accepted_by": None,
            "accepted_at": None,
            "overrides": 0,
        },
        consistency={
            "scope": consistency_spec.get("scope"),
            "field_scoped": bool(consistency_spec.get("field_scoped")),
            "kid": consistency_spec.get("kid") or job.kid,
            "seed_fingerprint": consistency_spec.get("seed_fingerprint"),
            "key_custody": inputs.key_custody,
        },
        columns=columns,
        reveal_map=reveal_map,
        counts={
            "rows_estimated": int(progress.rows_estimated or 0),
            "rows_read": int(progress.rows_read),
            "rows_written": rows_written,
            "rows_skipped": int(progress.rows_skipped),
            "partitions": len(inputs.partitions),
            "batches": sum(p.batch_no for p in inputs.partitions),
            "resumed_partitions": sum(1 for p in inputs.partitions if p.resumed > 0),
            "values_transformed": sum(v for k, v in methods.items() if k != "keep"),
        },
        verification=verification,
        fidelity=fidelity,
        privacy=privacy,
        utility=utility,
        tabular=tabular,
        timings={
            "started_at": started.isoformat(),
            "finished_at": now.isoformat(),
            "duration_s": round(duration, 3),
            "rows_per_s": round(rows_written / duration, 1) if duration > 0 else None,
        },
        partitions=[
            {
                "no": p.partition_no,
                "rows": p.rows_written,
                "attempts": p.attempts,
                "resumed": p.resumed,
                "state": str(p.state),
            }
            for p in inputs.partitions
        ],
        errors=list(inputs.errors),
        config_hash=_sha(
            policy["hash"] + (snapshot_hash or "") + hashlib.sha256(_canon(spec)).hexdigest()
        ),
    )


_CSS = """:root{--ink:#141a2b;--ink2:#4d5875;--line:#e0e5f0;--teal:#0f766e;--emerald:#047857;\
--rose:#be123c;--amber:#b45309}
*{box-sizing:border-box}body{font:13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,\
Arial,sans-serif;color:var(--ink);margin:0;background:#fff}
main{max-width:980px;margin:0 auto;padding:32px 28px}h1{font-size:22px;margin:4px 0}
h2{font-size:15px;margin:28px 0 10px;border-bottom:1px solid var(--line);padding-bottom:6px}
.muted{color:var(--ink2)}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;\
font-size:12px;word-break:break-all}
.chip{display:inline-block;border:1px solid;border-radius:999px;padding:1px 8px;font-size:11px;\
font-weight:700}
.ok{color:var(--emerald)}.bad{color:var(--rose)}.warn{color:var(--amber)}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
.kpi{border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.kpi b{display:block;font-size:20px}table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--ink2)}
.sig{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}
.sig div{border:1px solid var(--line);border-radius:10px;padding:12px;min-height:90px}
@media (max-width:640px){.kpis,.sig{grid-template-columns:1fr 1fr}main{padding:16px}}
@media print{main{padding:0}h2{break-after:avoid}table,.kpis,.sig{break-inside:avoid}}"""


def _cell(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, dict | list):
        return html.escape(json.dumps(value, sort_keys=True, ensure_ascii=False))
    return html.escape(str(value))


def render_html(pack: EvidencePack, *, sha: str) -> str:
    """The printable evidence page (mockup 10's sections M2 can fill); no scripts."""
    e = _cell
    v = pack.verification
    hits = v.get("hits", 0)
    # packs stored before N3 could say passed for a failed run: the outcome decides
    passed = bool(v.get("passed")) and pack.outcome in FINISHED_OUTCOMES
    post = v.get("post_sample") or {}
    if passed:
        status = f"<span class='chip ok'>verified · {e(hits)} leaks</span>"
    elif not hits and pack.outcome not in FINISHED_OUTCOMES:
        first = pack.errors[0].get("code") if pack.errors else None
        status = (
            f"<span class='chip bad'>not verified · job {e(pack.outcome)}"
            f"{f' ({e(first)})' if first else ''}</span>"
        )
    elif v.get("review_required") and not hits and post.get("error"):
        status = (
            "<span class='chip warn'>review required · the post-write sample did not run "
            f"({e(post.get('error'))})</span>"
        )
    elif v.get("review_required") and not hits:
        status = (
            f"<span class='chip warn'>review required · {e(post.get('kept_column_hits', 0))} "
            "findings in columns kept as not personal</span>"
        )
    else:
        status = f"<span class='chip bad'>verification failed · {e(hits)} hits</span>"
    sweep_failed = bool(hits) or (not passed and not v.get("review_required"))
    if v.get("review_required") and post.get("error"):
        # S3 review round 2: a sample that could not run is no clean sample (fail closed)
        kept_note = (
            "<tr><th>Review required</th><td class=warn>The post-write sample did not run "
            f"({e(post.get('error'))}), so nothing checked the written output for personal data "
            "in columns kept as not personal. Check the output before using it.</td></tr>"
        )
    elif v.get("review_required"):
        kept_note = (
            "<tr><th>Review required</th><td class=warn>The post-write sample found personal data "
            f"in {e(', '.join(post.get('kept_columns') or []))}, which the snapshot kept as not "
            "personal. Reclassify these columns and run again before using the output.</td></tr>"
        )
    else:
        kept_note = ""
    sel = pack.source.get("selection") or {}
    cols = (
        "".join(
            f"<tr><td class=mono>{e(c.get('path'))}</td><td>{e(c.get('entity_type'))}</td>"
            f"<td class=mono>{e(c.get('method'))}</td>"
            f"<td>{'yes' if c.get('reversible') else 'one-way'}</td><td>{e(c.get('values'))}</td></tr>"
            for c in pack.columns
        )
        or "<tr><td colspan=5 class=muted>No column was planned.</td></tr>"
    )
    parts = (
        "".join(
            f"<tr><td>p{e(p.get('no'))}</td><td>{e(p.get('rows'))}</td><td>{e(p.get('attempts'))}</td>"
            f"<td>{e(p.get('resumed'))}</td><td>{e(p.get('state'))}</td></tr>"
            for p in pack.partitions
        )
        or "<tr><td colspan=5 class=muted>No partition ran.</td></tr>"
    )
    errors = "".join(
        f"<tr><td class=mono>{e(x.get('code'))}</td><td>{e(x.get('where'))}</td></tr>"
        for x in pack.errors
    )
    purpose = pack.purpose
    custody = pack.consistency.get("key_custody") or {}
    if pack.label == LABEL_OBFUSCATE:
        note = (
            f"Whoever holds the key with id {e(pack.consistency.get('kid'))} can re-identify "
            "every row. Hold this output under the same controls as the source."
        )
    else:
        note = (
            "The synthetic output is a measurement, not a claim of anonymity. Treat it as "
            "confidential until your data protection officer decides otherwise."
        )
    target_text = pack.target.get("object") or (pack.target.get("files") or {}).get("prefix")
    files = pack.target.get("files") or {}
    files_row = (
        f"<tr><th>Files</th><td>{e(files.get('format'))} · {e(files.get('objects'))} objects · "
        f"{e(files.get('rows'))} rows · manifest {e(files.get('manifest_artefact_id'))}</td></tr>\n"
        if files
        else ""
    )
    fidelity = (
        f"<h2>6 Synthetic fidelity and privacy</h2><table><tr><th>Fidelity</th>"
        f"<td class=mono>{e(pack.fidelity)}</td></tr><tr><th>Privacy</th>"
        f"<td class=mono>{e(pack.privacy)}</td></tr><tr><th>Utility</th>"
        f"<td class=mono>{e(pack.utility)}</td></tr><tr><th>Tabular model</th>"
        f"<td class=mono>{e(pack.tabular)}</td></tr></table>"
        if pack.fidelity is not None or pack.privacy is not None
        else ""
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DPS evidence pack {e(pack.id)}</title><style>{_CSS}</style></head><body><main>
<div class=muted>Data Privacy Services · evidence pack</div>
<h1>{e(pack.source.get("engine"))} {e(pack.source.get("object"))} → {e(target_text or pack.target.get("write_mode"))}</h1>
<p>Job <span class=mono>{e(pack.job_id)}</span> · {e(pack.mode)} · {e(pack.target.get("write_mode"))} · outcome <b>{e(pack.outcome)}</b> · {status}</p>
<p>Pack <span class=mono>{e(pack.id)}</span> · sha256 <span class=mono>{e(sha)}</span> · sealed {e(pack.created_at.isoformat())}</p>
<p><span class=chip>{e(pack.label)}</span></p>
<p>Purpose {e(purpose.ticket if purpose else None)} · {e(purpose.legal_basis if purpose else None)} · requested by key {e(pack.requested_by)}</p>
<h2>1 Summary</h2><div class=kpis>
<div class=kpi>Rows written<b>{e(pack.counts.get("rows_written", 0))}</b><span class=muted>of {e(pack.counts.get("rows_read", 0))} read</span></div>
<div class=kpi>Columns transformed<b>{len(pack.columns)}</b><span class=muted>{e(pack.counts.get("values_transformed", 0))} values</span></div>
<div class=kpi>Leakage sweep<b class="{"bad" if sweep_failed else "ok"}">{e(hits)} hits</b><span class=muted>{e(v.get("values_checked", 0))} values checked</span></div>
<div class=kpi>Raw values to LLM<b class=ok>0</b><span class=muted>{e(pack.timings.get("duration_s"))} s · {e(pack.counts.get("partitions", 0))} partitions</span></div></div>
<h2>2 Configuration hashes</h2><table>
<tr><th>Source</th><td>{e(pack.source.get("connection_name"))} · {e(pack.source.get("engine"))} {e(pack.source.get("server_version"))} · {e(pack.source.get("object"))}</td></tr>
<tr><th>Target</th><td>{e(pack.target.get("connection_name"))} · {e(target_text)} · {e(pack.target.get("write_mode"))}{" · created" if pack.target.get("created") else ""}{" · truncated" if pack.target.get("truncated") else ""}</td></tr>
{files_row}<tr><th>Policy</th><td class=mono>{e(pack.policy.get("name"))} · {e(pack.policy.get("hash"))}</td></tr>
<tr><th>Snapshot</th><td class=mono>{e(pack.snapshot.get("id"))} · {e(pack.snapshot.get("content_hash"))} · accepted by {e(pack.snapshot.get("accepted_by"))} · {e(pack.snapshot.get("overrides"))} overrides</td></tr>
<tr><th>Configuration</th><td class=mono>{e(pack.config_hash)}</td></tr>
<tr><th>Engine</th><td class=mono>{e(pack.engine_version)} · image {e(pack.image_digest or "unset")}</td></tr>
<tr><th>Key</th><td class=mono>kid {e(pack.consistency.get("kid"))} · seed {e(pack.consistency.get("seed_fingerprint"))} · returned to the caller: {e(custody.get("returned"))} · DPS stored no key</td></tr>
<tr><th>Selection</th><td class=mono>{e(sel.get("where_template") or sel.get("query_template") or "all rows")} · {e(sel.get("binds", 0))} bound parameters</td></tr></table>
<h2>3 Classification snapshot</h2><table><tr><th>Column</th><th>Entity</th><th>Method</th><th>Reversible</th><th>Values</th></tr>{cols}</table>
<h2>4 Execution</h2><table><tr><th>Partition</th><th>Rows</th><th>Attempts</th><th>Resumed</th><th>Result</th></tr>{parts}</table>
<p class=muted>Checkpoint positions stay in DPS's job tables and are not printed here: a key position is a customer identifier.</p>
{f"<table><tr><th>Error</th><th>Where</th></tr>{errors}</table>" if errors else ""}
<h2>5 Verification</h2><table>
<tr><th>Leakage sweep</th><td>{e(hits)} hits in {e(v.get("values_checked", 0))} values ({e(v.get("mode"))})</td></tr>
<tr><th>Post-write sample</th><td>{e(v.get("post_sample"))}</td></tr>{kept_note}
<tr><th>Row counts</th><td>{e(pack.counts.get("rows_read", 0))} read · {e(pack.counts.get("rows_written", 0))} written · {e(pack.counts.get("rows_skipped", 0))} skipped</td></tr>
<tr><th>Fallbacks</th><td>{e(v.get("fallbacks"))}</td></tr></table>
{fidelity}
<h2>7 Regulatory note</h2><p>{note} DPS stores no customer values and no keys; this pack holds counts, hashes, names and key ids only.</p>
<h2>8 Signatures</h2><div class=sig><div><b>System attestation</b><br>Data Privacy Services {e(pack.engine_version)}<br><span class=mono>sha256:{e(sha)}</span></div>
<div><b>Data owner</b><br><br>Signature · Date</div><div><b>Data protection officer</b><br><br>Signature · Date</div><div><b>Change approver</b><br><br>Signature · Date</div></div>
</main></body></html>"""


async def save_evidence(
    pack: EvidencePack,
    *,
    artefacts: ArtefactService,
    acquire: AcquireFn,
    expires_at: datetime | None,
) -> EvidenceRecord:
    """Insert the pack row, store its JSON and HTML renderings as artefacts, link the HTML."""
    sha = pack_sha256(pack)
    s = db.schema_ident()
    async with acquire(pack.tenant_id) as conn:
        row = await conn.fetchrow(
            f"INSERT INTO {s}.dps_evidence (id, tenant_id, job_id, pack, pack_sha256, "
            "pack_version, expires_at) VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7) "
            "RETURNING created_at",
            pack.id,
            pack.tenant_id,
            pack.job_id,
            pack.model_dump(mode="json"),
            sha,
            PACK_VERSION,
            expires_at,
        )
    meta = {"evidence_id": pack.id}
    json_id = await artefacts.save(
        tenant_id=pack.tenant_id,
        job_id=pack.job_id,
        kind="evidence_json",
        data=canonical_json(pack),
        content_type="application/json",
        meta=meta,
    )
    html_id = await artefacts.save(
        tenant_id=pack.tenant_id,
        job_id=pack.job_id,
        kind="evidence_html",
        data=render_html(pack, sha=sha).encode("utf-8"),
        content_type="text/html; charset=utf-8",
        meta=meta,
    )
    async with acquire(pack.tenant_id) as conn:
        await conn.execute(
            f"UPDATE {s}.dps_evidence SET html_artefact_id = $2 WHERE id = $1", pack.id, html_id
        )
    return EvidenceRecord(
        id=pack.id,
        job_id=pack.job_id,
        pack=pack,
        pack_sha256=sha,
        html_artefact_id=html_id,
        json_artefact_id=json_id,
        created_at=row["created_at"],
    )


_JSON_ARTEFACT = (
    "(SELECT a.id FROM {s}.dps_artefact a WHERE a.tenant_id = e.tenant_id AND a.job_id = e.job_id "
    "AND a.kind = 'evidence_json' AND a.meta->>'evidence_id' = e.id "
    "ORDER BY a.created_at DESC LIMIT 1) AS json_artefact_id"
)


async def get_evidence(tenant_id: str, evidence_id: str) -> EvidenceRecord | None:
    """The tenant's pack with its hash and artefact ids (API pool, RLS-scoped), or None."""
    s = db.schema_ident()
    async with db.acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT e.id, e.job_id, e.pack, e.pack_sha256, e.html_artefact_id, "
            f"e.pdf_artefact_id, e.created_at, {_JSON_ARTEFACT.format(s=s)} "
            f"FROM {s}.dps_evidence e WHERE e.tenant_id = $1 AND e.id = $2",
            tenant_id,
            evidence_id,
        )
    if row is None:
        return None
    data = dict(row)
    data["pack"] = EvidencePack.model_validate(data["pack"])
    return EvidenceRecord.model_validate(data)


async def list_evidence(
    tenant_id: str,
    *,
    job_id: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> tuple[list[EvidenceSummary], str | None]:
    """Newest first; keyset pagination on ``(created_at, id)``. ``ValueError`` on a bad cursor."""
    s = db.schema_ident()
    params: list[Any] = [tenant_id]
    where = ["tenant_id = $1"]
    if job_id is not None:
        params.append(job_id)
        where.append(f"job_id = ${len(params)}")
    if cursor:
        ts, eid = decode_cursor(cursor)
        params += [ts, eid]
        where.append(f"(created_at, id) < (${len(params) - 1}::timestamptz, ${len(params)})")
    page = max(_MIN_LIMIT, min(_MAX_LIMIT, limit))
    async with db.acquire(tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT id, job_id, created_at, html_artefact_id, pack->>'outcome' AS outcome, "
            "pack->>'mode' AS mode, pack->>'label' AS label, "
            "pack->'source'->>'object' AS source_object, "
            "COALESCE(pack->'target'->>'object', pack->'target'->'files'->>'prefix') AS target, "
            "pack->'target'->>'write_mode' AS write_mode, pack->'policy'->>'name' AS policy, "
            "pack->'purpose'->>'ticket' AS ticket, "
            # a failed or canceled run never passed (packs stored before N3 could say it did)
            "(COALESCE((pack->'verification'->>'passed')::boolean, false) "
            "AND pack->>'outcome' IN ('succeeded', 'dry_run')) AS passed, "
            "pack->'errors'->0->>'code' AS error_code, "
            "COALESCE((pack->'verification'->>'review_required')::boolean, false) "
            "AS review_required, "
            "COALESCE((pack->'verification'->>'hits')::int, 0) AS hits, "
            "COALESCE((pack->'counts'->>'rows_written')::bigint, 0) AS rows_written "
            f"FROM {s}.dps_evidence WHERE {' AND '.join(where)} "
            f"ORDER BY created_at DESC, id DESC LIMIT {page + 1}",
            *params,
        )
    items = [EvidenceSummary.model_validate(dict(r)) for r in rows[:page]]
    nxt = encode_cursor(items[-1].created_at, items[-1].id) if len(rows) > page and items else None
    return items, nxt
