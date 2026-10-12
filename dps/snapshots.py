"""Classification snapshots (contract §5.4): create, read, list, review (overrides), accept.

A snapshot is the per-column classification of one object, proposed by a discover job and
reviewed by the data owner before any run may use it (``POST /jobs/run`` needs an ``accepted``
snapshot). Rows live in ``dps_snapshot`` under the tenant GUC; they hold value **shapes** only,
never values.

``columns`` stores the effective classification: ``method`` is the accepted method (the
suggestion unless overridden) and ``override`` marks every column an owner changed.
**Overrides accumulate per path**: each :func:`set_overrides` call merges its overrides into the
stored list (fields given later win for the same path; a path's override moves to the end), and
the merged list is applied again to the columns from their suggestions. An override, once made,
therefore stays visible (``override=True``, and in the evidence pack) until another override for
the same path replaces it. An explicitly ``null`` ``entity_type`` marks a column not sensitive.

Accepting supersedes the previously accepted snapshot of the same ``(connection, object)`` in the
same transaction; the partial unique index ``dps_snapshot_one_accepted`` turns a lost race into
``snapshot_conflict``. Errors are :class:`SnapshotStateError` (code, HTTP status, value-free
detail).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any, cast

import asyncpg

from dps import db
from dps.engine.policy import MethodSpec, Policy, load_preset, preset_names
from dps.engine.taxonomy import entity_info
from dps.jobs import decode_cursor, encode_cursor
from dps.jobs_models import ClassificationSnapshot, ColumnClassification, SnapshotOverride
from dps.layers.connectors.base import ColumnInfo, ColumnKind, TableShape

_COLS = (
    "id, tenant_id, job_id, connection_id, engine, object_ref, status, policy_name, policy_hash, "
    "shape, columns, sample_rows, overrides, content_hash, created_at, accepted_at, accepted_by"
)
_MIN_LIMIT, _MAX_LIMIT = 1, 200
_KINDS = frozenset(
    {
        "int",
        "decimal",
        "float",
        "text",
        "char",
        "date",
        "timestamp",
        "bool",
        "binary",
        "json",
        "lob_text",
        "lob_binary",
        "uuid",
        "object_id",
        "other",
    }
)
FREE_TEXT = "FREE_TEXT"
#: A column whose values are personal data of several types (an e-mail in one row, a phone
#: number in the next): every value is replaced whole (integrated verify r1, B1).
MIXED_PII = "MIXED_PII"


class SnapshotStateError(Exception):
    """A refused snapshot operation: ``code`` and ``status`` map to ``{"detail", "code"}``."""

    def __init__(self, code: str, status: int, detail: str) -> None:
        super().__init__(detail)
        self.code, self.status, self.detail = code, status, detail


def _override_dump(override: SnapshotOverride) -> dict[str, Any]:
    """The fields the caller set (an explicit ``null`` differs from "not overridden")."""
    return override.model_dump(mode="json", exclude_unset=True)


def content_hash(columns: list[ColumnClassification], overrides: list[SnapshotOverride]) -> str:
    """sha256 of the canonical JSON of the columns and the overrides (evidence, run plan)."""
    doc = {
        "columns": [c.model_dump(mode="json") for c in columns],
        "overrides": [_override_dump(o) for o in overrides],
    }
    canon = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def merge_overrides(
    stored: list[SnapshotOverride], new: list[SnapshotOverride]
) -> list[SnapshotOverride]:
    """Per-path merge: fields set in ``new`` win; a re-specified path moves to the end."""
    merged: dict[str, SnapshotOverride] = {o.path: o for o in stored}
    for override in new:
        old = merged.pop(override.path, None)
        if old is not None:
            data = _override_dump(old)
            data.update(_override_dump(override))
            override = SnapshotOverride.model_validate(data)
        merged[override.path] = override
    return list(merged.values())


def _column_info(column: ColumnClassification, shape: TableShape | None) -> ColumnInfo:
    if shape is not None:
        try:
            return shape.column(column.name)
        except KeyError:
            pass
    kind = column.kind if column.kind in _KINDS else "other"
    return ColumnInfo(
        path=column.path,
        name=column.name,
        data_type=column.data_type,
        kind=cast(ColumnKind, kind),
        is_key=column.is_key,
    )


def _reclassify(
    column: ColumnClassification,
    override: SnapshotOverride,
    *,
    policy: Policy | None,
    fpe_enabled: bool,
    shape: TableShape | None,
) -> dict[str, Any]:
    """The column fields an entity / free-text override changes (methods re-suggested)."""
    from dps.layers.connectors.discovery import suggest_methods  # discovery imports this module

    fields = override.model_fields_set
    entity = override.entity_type if "entity_type" in fields else column.entity_type
    text = column.text_column
    if override.text_column is not None:
        text = override.text_column
        if text and entity is None:
            entity = FREE_TEXT
        if not text and entity == FREE_TEXT:
            entity = None
    elif "entity_type" in fields:
        text = entity == FREE_TEXT
    if entity is None:
        obf, syn, note = MethodSpec(method="keep"), MethodSpec(method="keep"), None
    elif policy is None:
        if override.method is None:
            raise SnapshotStateError(
                "override_method_required",
                422,
                "the snapshot's policy is not a preset: a reclassified column needs a method",
            )
        obf, note = override.method, None
        syn = override.synthesize_method or MethodSpec(method="surrogate")
    else:
        obf, syn, note = suggest_methods(
            entity,
            _column_info(column, shape),
            policy=policy,
            fpe_enabled=fpe_enabled,
            min_digits=None,
            text_column=text,
        )
    return {
        "entity_type": entity,
        "text_column": text,
        "sensitivity": entity_info(entity).sensitivity if entity else None,
        "suggested": obf,
        "method": obf,
        "synthesize_suggested": syn,
        "synthesize_method": syn,
        "type_note": note,
        "anchor": column.anchor and entity is not None,
    }


def apply_overrides(
    columns: list[ColumnClassification],
    overrides: list[SnapshotOverride],
    *,
    policy: Policy | None,
    fpe_enabled: bool,
    shape: TableShape | None = None,
) -> list[ColumnClassification]:
    """Columns reset to their suggestions, then every override applied in order.

    ``entity_type``/``text_column`` changes re-suggest the methods for the new classification
    (``policy`` required, else the override must carry a ``method``); ``method`` and
    ``synthesize_method`` replace the column's; ``anchor=true`` moves the anchor here. Every
    touched column gets ``override=True``.

    Raises:
        SnapshotStateError: ``override_unknown_path``, ``override_method_required`` or
            ``override_anchor_invalid`` (an anchor on a column without an entity), all 422.
    """
    index = {c.path: i for i, c in enumerate(columns)}
    for override in overrides:
        if override.path not in index:
            raise SnapshotStateError(
                "override_unknown_path", 422, "an override names a path the snapshot does not have"
            )
    out = [
        c.model_copy(
            update={
                "method": c.suggested,
                "synthesize_method": c.synthesize_suggested,
                "override": False,
            }
        )
        for c in columns
    ]
    for override in overrides:
        i = index[override.path]
        column = out[i]
        update: dict[str, Any] = {"override": True}
        if "entity_type" in override.model_fields_set or override.text_column is not None:
            update.update(
                _reclassify(column, override, policy=policy, fpe_enabled=fpe_enabled, shape=shape)
            )
        if override.method is not None:
            update["method"] = override.method
        if override.synthesize_method is not None:
            update["synthesize_method"] = override.synthesize_method
        column = column.model_copy(update=update)
        if override.anchor is not None:
            if override.anchor and column.entity_type is None:
                raise SnapshotStateError(
                    "override_anchor_invalid", 422, "the anchor must be a classified column"
                )
            if override.anchor:
                out = [c.model_copy(update={"anchor": False}) if c.anchor else c for c in out]
            column = column.model_copy(update={"anchor": override.anchor})
        out[i] = column
    return out


@asynccontextmanager
async def _using(
    tenant_id: str, conn: asyncpg.Connection | None
) -> AsyncIterator[asyncpg.Connection]:
    if conn is not None:
        yield conn
    else:
        async with db.acquire(tenant_id) as fresh:
            yield fresh


def _row(row: Mapping[str, Any]) -> ClassificationSnapshot:
    return ClassificationSnapshot.model_validate(dict(row))


def _policy_for(snapshot: ClassificationSnapshot) -> Policy | None:
    """The snapshot's preset, when its name and hash still match one (inline policies: None)."""
    if snapshot.policy_name not in preset_names():
        return None
    preset = load_preset(snapshot.policy_name)
    return preset if preset.config_hash() == snapshot.policy_hash else None


async def create_snapshot(
    snapshot: ClassificationSnapshot, *, conn: asyncpg.Connection | None = None
) -> None:
    """Insert a snapshot row (shapes only; the sample it came from is already gone)."""
    async with _using(snapshot.tenant_id, conn) as c:
        await c.execute(
            f"INSERT INTO {db.schema_ident()}.dps_snapshot (id, tenant_id, job_id, connection_id, "
            "engine, object_ref, status, policy_name, policy_hash, shape, columns, sample_rows, "
            "overrides, content_hash, created_at, accepted_at, accepted_by) VALUES ($1, $2, $3, $4, "
            "$5, $6, $7, $8, $9, $10::jsonb, $11::jsonb, $12, $13::jsonb, $14, $15, $16, $17)",
            snapshot.id,
            snapshot.tenant_id,
            snapshot.job_id,
            snapshot.connection_id,
            snapshot.engine,
            snapshot.object_ref,
            snapshot.status,
            snapshot.policy_name,
            snapshot.policy_hash,
            snapshot.shape.model_dump(mode="json"),
            [c.model_dump(mode="json") for c in snapshot.columns],
            snapshot.sample_rows,
            [_override_dump(o) for o in snapshot.overrides],
            snapshot.content_hash,
            snapshot.created_at,
            snapshot.accepted_at,
            snapshot.accepted_by,
        )


async def get_snapshot(
    tenant_id: str, snapshot_id: str, *, conn: asyncpg.Connection | None = None
) -> ClassificationSnapshot | None:
    """The tenant's snapshot (RLS-scoped), or None."""
    async with _using(tenant_id, conn) as c:
        row = await c.fetchrow(
            f"SELECT {_COLS} FROM {db.schema_ident()}.dps_snapshot "
            "WHERE tenant_id = $1 AND id = $2",
            tenant_id,
            snapshot_id,
        )
    return _row(row) if row else None


async def list_snapshots(
    tenant_id: str,
    *,
    connection_id: str | None = None,
    object_ref: str | None = None,
    status: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> tuple[list[ClassificationSnapshot], str | None]:
    """Newest first; keyset pagination on ``(created_at, id)``. ``ValueError`` on a bad cursor."""
    params: list[Any] = [tenant_id]
    where = ["tenant_id = $1"]
    for column, value in (
        ("connection_id", connection_id),
        ("object_ref", object_ref),
        ("status", status),
    ):
        if value is not None:
            params.append(value)
            where.append(f"{column} = ${len(params)}")
    if cursor:
        ts, sid = decode_cursor(cursor)
        params += [ts, sid]
        where.append(f"(created_at, id) < (${len(params) - 1}::timestamptz, ${len(params)})")
    page = max(_MIN_LIMIT, min(_MAX_LIMIT, limit))
    async with db.acquire(tenant_id) as conn:
        rows = await conn.fetch(
            f"SELECT {_COLS} FROM {db.schema_ident()}.dps_snapshot WHERE {' AND '.join(where)} "
            f"ORDER BY created_at DESC, id DESC LIMIT {page + 1}",
            *params,
        )
    items = [_row(r) for r in rows[:page]]
    nxt = encode_cursor(items[-1].created_at, items[-1].id) if len(rows) > page and items else None
    return items, nxt


async def _locked(
    conn: asyncpg.Connection, tenant_id: str, snapshot_id: str
) -> ClassificationSnapshot:
    row = await conn.fetchrow(
        f"SELECT {_COLS} FROM {db.schema_ident()}.dps_snapshot "
        "WHERE tenant_id = $1 AND id = $2 FOR UPDATE",
        tenant_id,
        snapshot_id,
    )
    if row is None:
        raise SnapshotStateError("snapshot_not_found", 404, "no such snapshot")
    snapshot = _row(row)
    if snapshot.status != "proposed":
        raise SnapshotStateError(
            "snapshot_not_proposed", 409, "only a proposed snapshot can be reviewed or accepted"
        )
    return snapshot


async def set_overrides(
    tenant_id: str, snapshot_id: str, overrides: list[SnapshotOverride], *, fpe_enabled: bool
) -> ClassificationSnapshot:
    """Merge ``overrides`` into a proposed snapshot and re-apply them (module docstring).

    Raises:
        SnapshotStateError: ``snapshot_not_found`` 404, ``snapshot_not_proposed`` 409 and the
            :func:`apply_overrides` codes (422).
    """
    async with db.acquire(tenant_id) as conn, conn.transaction():
        snapshot = await _locked(conn, tenant_id, snapshot_id)
        merged = merge_overrides(snapshot.overrides, overrides)
        columns = apply_overrides(
            snapshot.columns,
            merged,
            policy=_policy_for(snapshot),
            fpe_enabled=fpe_enabled,
            shape=snapshot.shape,
        )
        digest = content_hash(columns, merged)
        await conn.execute(
            f"UPDATE {db.schema_ident()}.dps_snapshot SET columns = $3::jsonb, "
            "overrides = $4::jsonb, content_hash = $5 WHERE tenant_id = $1 AND id = $2",
            tenant_id,
            snapshot_id,
            [c.model_dump(mode="json") for c in columns],
            [_override_dump(o) for o in merged],
            digest,
        )
    return snapshot.model_copy(
        update={"columns": columns, "overrides": merged, "content_hash": digest}
    )


async def accept_snapshot(
    tenant_id: str, snapshot_id: str, *, accepted_by: str
) -> ClassificationSnapshot:
    """Accept a proposed snapshot, superseding the accepted one of the same connection+object.

    Raises:
        SnapshotStateError: ``snapshot_not_found`` 404, ``snapshot_not_proposed`` 409, or
            ``snapshot_conflict`` 409 when a concurrent accept won the race.
    """
    s = db.schema_ident()
    try:
        async with db.acquire(tenant_id) as conn, conn.transaction():
            snapshot = await _locked(conn, tenant_id, snapshot_id)
            await conn.execute(
                f"UPDATE {s}.dps_snapshot SET status = 'superseded' WHERE tenant_id = $1 "
                "AND connection_id = $2 AND object_ref = $3 AND status = 'accepted'",
                tenant_id,
                snapshot.connection_id,
                snapshot.object_ref,
            )
            row = await conn.fetchrow(
                f"UPDATE {s}.dps_snapshot SET status = 'accepted', accepted_at = now(), "
                f"accepted_by = $3 WHERE tenant_id = $1 AND id = $2 RETURNING {_COLS}",
                tenant_id,
                snapshot_id,
                accepted_by,
            )
    except asyncpg.UniqueViolationError:
        raise SnapshotStateError(
            "snapshot_conflict", 409, "another snapshot of this object was accepted concurrently"
        ) from None
    if row is None:  # the row was locked above; RLS hides it only if the GUC binding is broken
        raise RuntimeError("accepted snapshot is not visible to its tenant")
    return _row(row)
