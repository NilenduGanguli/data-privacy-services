"""Discovery: sample → detect → per-column classification snapshot (spec §7, contract §5.4).

For every column, each sampled non-null value is run through the M0 detector as a one-field
document ``{name: text}`` (structural T0 on the column name, T1 patterns and checksums, T2 NER when
enabled, T4 declared fields / custom recognizers). Per candidate entity ``E``:
``score(E) = noisy_or(name_score(E), whole_rate(E) × mean_score(E))``; the best ``E`` at or above
``policy.detect.min_score`` is the column's entity. Otherwise a ``text``/``lob_text`` column whose
values carry span findings in at least 5 % of the sampled rows is free text (``FREE_TEXT``, spans
planned per row at run time). Binary, LOB-binary and ObjectId columns are classified by name only
and always kept. The anchor (contract §6.2) is the first column, by M0's ``ANCHOR_ENTITIES``
priority (ties: the key column, then column order), whose entity is an anchor entity.

**Mixed columns** (integrated verify r1, B1). Per-entity scoring misses a column whose values are
personal data of several types — ``contacts.value`` holds an e-mail in one row and a mobile number
in the next, ``kyc_documents.doc_number`` an Aadhaar, an SSN or a CURP — because no single type
reaches the threshold. When no entity passes, the rows with *any* whole-value finding of MEDIUM or
higher sensitivity are counted together: ``noisy_or(name cue, any_rate × mean best score)`` at or
above ``min_score`` makes the column ``MIXED_PII``, and every value is replaced whole
(``det_token``; ``hmac_pseudonym`` on a bounded column, where a token would not fit;
``mask_partial`` below 16 characters). A column called ``value`` (``contact``, ``detail``, …)
next to a ``channel`` / ``contact_type`` column is a mixed contact value by its name. A kept
column whose sampled values still carry such findings in at least 5 % of the rows is never
presented as "not personal": it keeps ``value_entities`` and ``value_hit_rate`` and the note
``kept_with_hits`` (a decision for the reviewer). A numeric column below the bar is noise (random
12-digit ids pass the Aadhaar checksum one time in ten) and reports no hits.

Only value **shapes** (``Xxxxx Xxxxxxx``, ``9999 9999``) are kept; the sample never leaves the
worker and is dropped as soon as the snapshot is built. Errors carry codes only.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from pydantic import ValidationError

from dps import db, jobs
from dps.connections import ConnectionStoreError, get_connection, load_credentials
from dps.engine.batch import KEYLESS_METHODS
from dps.engine.detect.ensemble import Detector
from dps.engine.detect.structural import classify_key, normalize_key
from dps.engine.policy import MethodSpec, Policy
from dps.engine.service import EngineError
from dps.engine.taxonomy import Sensitivity, entity_info
from dps.engine.transform.apply import ANCHOR_ENTITIES
from dps.ids import PREFIX_SNAPSHOT, new_id
from dps.jobs import ClaimedJob, JobPhase, JobState
from dps.jobs_models import (
    ClassificationSnapshot,
    ColumnClassification,
    DiscoverSpec,
    SealedSelection,
    Selection,
)
from dps.layers.connectors.base import (
    NEVER_TRANSFORMED_KINDS,
    ColumnInfo,
    Connector,
    ConnectorError,
    Engine,
    Row,
    TableShape,
    to_engine_text,
)
from dps.layers.connectors.registry import open_connector
from dps.layers.connectors.selection import SelectionError, validate_selection
from dps.observability import get_logger
from dps.secrets import SealError, SecretUnavailable, open_json
from dps.snapshots import FREE_TEXT, MIXED_PII, content_hash, create_snapshot

if TYPE_CHECKING:
    from dps.worker import HandlerContext

logger = get_logger(__name__)

#: Keys of joins: kept in synthesize mode so synthetic copies still join (contract §5.3).
JOIN_ENTITIES = frozenset({"CUSTOMER_ID", "EMPLOYEE_ID", "CASE_ID"})
SHAPE_PRESERVING_METHODS = frozenset(
    {
        "fpe_digits",
        "fpe_text",
        "phone",
        "email",
        "date_shift",
        "mask_partial",
        "noise",
        "keep",
        "null",
        "surrogate",
    }
)
TEXT_OUTPUT_METHODS = frozenset(
    {
        "det_token",
        "hmac_pseudonym",
        "redact",
        "bucket",
        "dictionary",
        "external_tokenize",
        "generalize",
    }
)
NUMERIC_SAFE_METHODS = frozenset({"fpe_digits", "keep", "null", "noise"})
DATE_SAFE_METHODS = frozenset({"date_shift", "keep", "null", "surrogate"})
TEXT_SPAN_MIN_RATE = 0.05
#: A kept column whose values carry MEDIUM+ findings in this share of the rows is a decision.
KEPT_HIT_MIN_RATE = 0.05
#: ``hmac_pseudonym``'s output length: a mixed value in a bounded column at least this long.
PSEUDONYM_LENGTH = 16
#: Name evidence that a column is a polymorphic contact value: its name, next to a column that
#: says which channel each row is (``contacts(channel, value)``).
MIXED_VALUE_NAMES = frozenset(
    {"value", "val", "contact", "contact value", "contact detail", "contact details", "detail"}
)
CHANNEL_NAMES = frozenset(
    {"channel", "contact channel", "contact type", "contact method", "medium", "channel type"}
)
MIXED_NAME_SCORE = 0.7
_TEXT_KINDS = frozenset({"text", "lob_text"})
_NUMERIC_KINDS = frozenset({"int", "decimal", "float"})
_DIGITS = re.compile(r"\d")
_SHAPE_MAX = 24


def value_shape(text: str) -> str:
    """Letters → ``x``/``X``, digits → ``9``, other characters kept; at most 24 characters."""
    shaped = "".join(
        "9" if ch.isdigit() else ("X" if ch.isupper() else "x") if ch.isalpha() else ch
        for ch in text
    )
    return shaped if len(shaped) <= _SHAPE_MAX else shaped[:_SHAPE_MAX] + "…"


def _noisy_or(*scores: float) -> float:
    acc = 1.0
    for s in scores:
        acc *= 1.0 - s
    return round(1.0 - acc, 3)


def suggest_methods(
    entity: str | None,
    column: ColumnInfo,
    *,
    policy: Policy,
    fpe_enabled: bool,
    min_digits: int | None,
    text_column: bool,
) -> tuple[MethodSpec, MethodSpec, str | None]:
    """``(obfuscate, synthesize, type_note)`` for one column.

    The policy's methods, then: FPE off → ``det_token`` (``fpe_disabled``); ``fpe_digits`` on fewer
    than 6 sampled digits → ``det_token`` (``fpe_domain_too_small``); numeric columns accept only
    ``fpe_digits``/``keep``/``null``/``noise`` (else ``keep`` for keys, ``numeric_key_kept``,
    ``null`` when nullable, else ``keep``, ``numeric_column``); date columns accept
    ``date_shift``/``keep``/``null``/``surrogate`` (else ``date_shift``, ``date_column``);
    ``uuid``/``bool`` → ``keep`` (``type_kept``). Synthesize mode keeps key and join columns
    (``synthesize_key_kept``), nulls or keeps sensitive numerics (``synthesize_numeric``) and
    only uses keyless methods (else ``surrogate``).
    """
    keep = MethodSpec(method="keep")
    if entity is None:
        return keep, keep, None
    if column.kind in NEVER_TRANSFORMED_KINDS:
        return keep, keep, "binary_kept"
    if text_column:
        return MethodSpec(method="det_token"), MethodSpec(method="surrogate"), "text_pipeline"
    obf, syn = policy.method_for(entity, "obfuscate"), policy.method_for(entity, "synthesize")
    note: str | None = None
    if entity == MIXED_PII:
        obf, note = policy.treat.get(MIXED_PII) or mixed_method(column), "mixed_entities"
    if (
        policy.synthesize.keep_non_sensitive
        and entity not in policy.synthesize.overrides
        and entity_info(entity).sensitivity is Sensitivity.LOW
    ):
        syn = keep
    if obf.method in ("fpe_digits", "fpe_text") and not fpe_enabled:
        obf, note = MethodSpec(method="det_token"), "fpe_disabled"
    if obf.method == "fpe_digits" and min_digits is not None and min_digits < 6:
        obf, note = MethodSpec(method="det_token"), "fpe_domain_too_small"
    if column.kind in _NUMERIC_KINDS and obf.method not in NUMERIC_SAFE_METHODS:
        if column.is_key:
            obf, note = keep, note or "numeric_key_kept"
        else:
            obf, note = (
                (MethodSpec(method="null") if column.nullable else keep),
                (note or "numeric_column"),
            )
    if column.kind in ("date", "timestamp") and obf.method not in DATE_SAFE_METHODS:
        obf, note = MethodSpec(method="date_shift", params={"window_days": 365}), "date_column"
    if column.kind in ("uuid", "bool") and obf.method != "keep":
        obf, note = keep, "type_kept"
    if column.is_key or entity in JOIN_ENTITIES:
        syn, note = keep, note or "synthesize_key_kept"
    elif column.kind in _NUMERIC_KINDS and syn.method not in ("keep", "null"):
        syn, note = (
            (MethodSpec(method="null") if column.nullable else keep),
            (note or "synthesize_numeric"),
        )
    if syn.method not in KEYLESS_METHODS:
        syn = MethodSpec(method="surrogate")
    return obf, syn, note


def mixed_method(column: ColumnInfo) -> MethodSpec:
    """Whole-value replacement for a ``MIXED_PII`` column that fits it.

    ``det_token`` (reversible with the key) when the column is unbounded; on a bounded column a
    token would overflow (a worst case of ``ceil((4·L + 16)·8/5)`` characters), so a keyed
    ``hmac_pseudonym`` of :data:`PSEUDONYM_LENGTH` characters, and ``mask_partial`` (every letter
    and digit masked, length kept) when even that does not fit.
    """
    if column.length is None:
        return MethodSpec(method="det_token")
    if column.length >= PSEUDONYM_LENGTH:
        return MethodSpec(method="hmac_pseudonym", params={"length": PSEUDONYM_LENGTH})
    return MethodSpec(method="mask_partial", params={"keep_last": 0})


def _mixed_name_score(column: ColumnInfo, shape: TableShape) -> float:
    """:data:`MIXED_NAME_SCORE` for ``value`` (…) next to a ``channel`` (…) column, else 0."""
    if normalize_key(column.name) not in MIXED_VALUE_NAMES:
        return 0.0
    siblings = {normalize_key(c.name) for c in shape.columns if c.name != column.name}
    return MIXED_NAME_SCORE if siblings & CHANNEL_NAMES else 0.0


def _sensitive(entity: str) -> bool:
    return entity_info(entity).sensitivity.rank >= Sensitivity.MEDIUM.rank


def classify_columns(
    shape: TableShape, rows: list[Row], *, policy: Policy, detector: Detector, fpe_enabled: bool
) -> list[ColumnClassification]:
    """One :class:`ColumnClassification` per column of ``shape`` (no values retained)."""
    out: list[ColumnClassification] = []
    for column in shape.columns:
        by_name_only = column.kind in NEVER_TRANSFORMED_KINDS
        texts = [] if by_name_only else [to_engine_text(r.get(column.name), column) for r in rows]
        values = [t for t in texts if t]
        present = (
            sum(1 for r in rows if r.get(column.name) is not None) if by_name_only else len(values)
        )
        whole: dict[str, list[float]] = defaultdict(list)
        detectors: dict[str, set[str]] = defaultdict(set)
        span_rows = 0
        span_detectors: set[str] = set()
        # rows with a MEDIUM+ whole-value finding of any type (their best score), rows with a
        # MEDIUM+ finding anywhere, and the types and detectors behind them
        any_whole: list[float] = []
        sensitive_rows = 0
        seen: set[str] = set()
        seen_detectors: set[str] = set()
        for text in values:
            result = detector.detect(data={column.name: text})
            spans = [f for f in result.findings if not f.locator.whole]
            for f in result.findings:
                if f.locator.whole:
                    whole[f.entity_type].append(f.score)
                    detectors[f.entity_type].update(d.split(":")[0] for d in f.detectors)
            if spans:
                span_rows += 1
                span_detectors.update(d.split(":")[0] for f in spans for d in f.detectors)
            sensitive = [f for f in result.findings if _sensitive(f.entity_type)]
            if sensitive:
                sensitive_rows += 1
                seen.update(f.entity_type for f in sensitive)
                seen_detectors.update(d.split(":")[0] for f in sensitive for d in f.detectors)
            best_whole = [f.score for f in sensitive if f.locator.whole]
            if best_whole:
                any_whole.append(max(best_whole))
        names = {k.entity_type: k.score for k in classify_key(column.path)}
        scores: dict[str, float] = {}
        for candidate in set(whole) | set(names):
            hits = whole.get(candidate, [])
            rate = len(hits) / len(values) if values else 0.0
            mean = sum(hits) / len(hits) if hits else 0.0
            scores[candidate] = _noisy_or(names.get(candidate, 0.0), rate * mean)
        best: tuple[str | None, float] = max(
            scores.items(), key=lambda kv: kv[1], default=(None, 0.0)
        )
        entity = best[0] if best[1] >= policy.detect.min_score else None
        confidence = best[1]
        span_rate = span_rows / len(values) if values else 0.0
        sensitive_rate = sensitive_rows / len(values) if values else 0.0
        text_column = False
        mixed = False
        if entity is None and not by_name_only:
            name_cue = _mixed_name_score(column, shape)
            any_rate = len(any_whole) / len(values) if values else 0.0
            any_mean = sum(any_whole) / len(any_whole) if any_whole else 0.0
            mixed_score = _noisy_or(name_cue, any_rate * any_mean)
            if mixed_score >= policy.detect.min_score:
                entity, confidence, mixed = MIXED_PII, mixed_score, True
        if entity is None and column.kind in _TEXT_KINDS and span_rate >= TEXT_SPAN_MIN_RATE:
            entity, text_column = FREE_TEXT, True
        if text_column:
            hit_rate, used = span_rate, span_detectors
        elif mixed:
            hit_rate = len(any_whole) / len(values) if values else 0.0
            used = seen_detectors | ({"structural"} if _mixed_name_score(column, shape) else set())
        elif entity is None and column.kind in _NUMERIC_KINDS:
            # ids and amounts below the bar: a 12-digit id is a Verhoeff-valid "Aadhaar" one time
            # in ten by chance — noise, not personal data (the post-write sample agrees)
            hit_rate, used, seen = 0.0, set(), set()
        elif entity is None:
            # kept: what the detectors still found in it (never presented as "not personal")
            hit_rate, used = sensitive_rate, seen_detectors
        else:
            hit_rate = len(whole.get(entity or "", [])) / len(values) if values else 0.0
            used = detectors.get(entity or "", set()) | (
                {"structural"} if entity in names else set()
            )
        digit_counts = [len(_DIGITS.findall(v)) for v in values]
        obf, syn, note = suggest_methods(
            entity,
            column,
            policy=policy,
            fpe_enabled=fpe_enabled,
            min_digits=min(digit_counts) if digit_counts else None,
            text_column=text_column,
        )
        if entity is None and seen and sensitive_rate >= KEPT_HIT_MIN_RATE:
            note = "kept_with_hits"
        sensitivity = entity_info(entity).sensitivity if entity else None
        if mixed and seen:
            sensitivity = Sensitivity.max_of(entity_info(e).sensitivity for e in seen)
        out.append(
            ColumnClassification(
                path=column.path,
                name=column.name,
                data_type=column.data_type,
                kind=column.kind,
                is_key=column.is_key,
                entity_type=entity,
                confidence=round(min(1.0, 0.5 + span_rate) if text_column else confidence, 3),
                sensitivity=sensitivity,
                detectors=sorted(used),
                value_hit_rate=round(hit_rate, 3),
                null_rate=round(1 - present / len(rows), 3) if rows else 0.0,
                distinct_ratio=round(len(set(values)) / len(values), 3) if values else 0.0,
                shapes=[s for s, _ in Counter(value_shape(v) for v in values).most_common(3)],
                text_column=text_column,
                anchor=False,
                suggested=obf,
                method=obf,
                synthesize_suggested=syn,
                synthesize_method=syn,
                type_note=note,
                value_entities=sorted(seen),
            )
        )
    for anchor_entity in ANCHOR_ENTITIES:
        candidates = [i for i, c in enumerate(out) if c.entity_type == anchor_entity]
        if candidates:
            chosen = sorted(candidates, key=lambda i: (not out[i].is_key, i))[0]
            out[chosen] = out[chosen].model_copy(update={"anchor": True})
            break
    return out


async def _fail(ctx: HandlerContext, job: ClaimedJob, code: str, message: str) -> None:
    await jobs.advance_job(
        ctx.worker_id,
        job.id,
        phase=JobPhase.discover,
        state=JobState.failed,
        error_code=code,
        error=message,
        progress={"outcome": "failed"},
    )


def _has_selection(sel: Selection) -> bool:
    return bool(sel.where or sel.query or sel.mongo_filter is not None or sel.params)


async def discover_job(ctx: HandlerContext, job: ClaimedJob) -> None:
    """Sample, classify and store one proposed snapshot per object (values never leave the worker).

    Failures end the job with a code (``selection_*``, ``object_not_found``,
    ``secret_unavailable``, ``connection_missing``, ``connection_role``, ``policy_invalid``,
    ``spec_invalid``, a connector code); a retryable connector failure requeues it with backoff
    (dead-lettered after ``max_attempts``). A lost lease or a cancel stops it quietly.
    """
    try:
        spec = DiscoverSpec.model_validate(job.spec)
    except ValidationError:
        await _fail(ctx, job, "spec_invalid", "the stored discover spec is not valid")
        return
    if job.spec_enc is None and _has_selection(spec.selection):
        await _fail(ctx, job, "secret_unavailable", "the sealed selection is missing")
        return
    try:
        sealed = (
            open_json(ctx.kek, job.spec_enc, SealedSelection, aad=f"dps_job.spec:{job.id}")
            if job.spec_enc
            else SealedSelection()
        )
    except SealError:
        await _fail(ctx, job, "secret_unavailable", "the sealed selection could not be opened")
        return
    selection = spec.selection.model_copy(update=sealed.model_dump())
    try:
        async with db.acquire_queue(job.tenant_id) as conn:
            connection = await get_connection(job.tenant_id, spec.connection_id, conn=conn)
            creds = (
                await load_credentials(connection, settings=ctx.settings, kek=ctx.kek, conn=conn)
                if connection is not None
                else None
            )
    except SecretUnavailable:
        await _fail(ctx, job, "secret_unavailable", "the connection's credentials do not resolve")
        return
    except ConnectionStoreError as exc:
        await _fail(ctx, job, exc.code, exc.detail)
        return
    if connection is None or creds is None:
        await _fail(ctx, job, "connection_missing", "the connection no longer exists")
        return
    if "source" not in connection.roles:
        await _fail(ctx, job, "connection_role", "the connection is not a source")
        return
    try:
        policy = ctx.engine.policy(spec.policy)
    except EngineError:
        await _fail(ctx, job, "policy_invalid", "the discover policy is not valid")
        return
    locale = policy.locale.default or ctx.settings.default_locale
    engine = cast(Engine, connection.engine)
    connector: Connector | None = None
    snapshot_ids: list[str] = []
    try:
        connector = await open_connector(
            connection,
            ctx.settings,
            action="discover",
            kek=ctx.kek,
            job_id=job.id,
            credentials=creds,
        )
        for index, obj in enumerate(spec.objects):
            shape = await connector.describe(obj)
            sel = selection if len(spec.objects) == 1 else Selection()
            validate_selection(sel, engine, shape)
            rows = await connector.sample(
                shape, sel, sel.params, spec.sample_rows or ctx.settings.discovery_sample_rows
            )
            columns = await asyncio.to_thread(
                classify_columns,
                shape,
                rows,
                policy=policy,
                detector=ctx.engine.detector(policy, locale),
                fpe_enabled=ctx.engine.fpe_enabled,
            )
            sampled = len(rows)
            del rows
            snapshot = ClassificationSnapshot(
                id=new_id(PREFIX_SNAPSHOT),
                tenant_id=job.tenant_id,
                job_id=job.id,
                connection_id=connection.id,
                engine=connection.engine,
                object_ref=shape.object.fqn(),
                status="proposed",
                policy_name=policy.name,
                policy_hash=policy.config_hash(),
                shape=shape,
                columns=columns,
                sample_rows=sampled,
                overrides=[],
                content_hash=content_hash(columns, []),
                created_at=datetime.now(UTC),
                accepted_at=None,
                accepted_by=None,
            )
            async with db.acquire_queue(job.tenant_id) as conn:
                await create_snapshot(snapshot, conn=conn)
            snapshot_ids.append(snapshot.id)
            still_ours = await jobs.advance_job(
                ctx.worker_id,
                job.id,
                phase=JobPhase.discover,
                state=JobState.running,
                progress={
                    "snapshot_ids": snapshot_ids,
                    "stage": f"{index + 1}/{len(spec.objects)} objects",
                },
            )
            if not still_ours:
                return  # canceled or reclaimed: stop quietly
        await jobs.advance_job(
            ctx.worker_id,
            job.id,
            phase=JobPhase.discover,
            state=JobState.succeeded,
            progress={"outcome": "succeeded", "snapshot_ids": snapshot_ids},
            purge_secret=True,
        )
        logger.info("discover_done", job_id=job.id, count=len(snapshot_ids))
    except SelectionError as exc:
        await _fail(ctx, job, exc.code, str(exc))
    except ConnectorError as exc:
        if exc.retryable:
            await jobs.retry_job(
                ctx.worker_id,
                job.id,
                error_code=exc.code,
                error=str(exc),
                backoff_base=ctx.settings.job_retry_base_seconds,
                backoff_cap=ctx.settings.job_retry_max_seconds,
            )
        else:
            await _fail(ctx, job, exc.code, str(exc))
    finally:
        if connector is not None:
            await connector.close()
