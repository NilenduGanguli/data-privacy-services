"""Job groups (contract §6.1): one key and one seed shared by the runs of a group.

``create_group`` generates a ``KeyMaterial`` and a ``SynthesisSeed`` and returns both **once**;
the ``dps_job_group`` row keeps only the key id, the key check value (public, F-13) and the seed
fingerprint (``sha256(seed)[:16]``),
so DPS never stores a group key. Every run naming the group must carry matching material
(``validate_run`` → ``group_key_mismatch``). Rows live under the tenant GUC.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import asyncpg
from pydantic import BaseModel, ConfigDict

from dps import db
from dps.connections import touch_tenant
from dps.engine.synth.surrogates import SynthesisSeed, generate_synthesis_seed
from dps.engine.transform.crypto import KeyMaterial, generate_key_material, key_check_value
from dps.ids import PREFIX_GROUP, new_id
from dps.jobs import decode_cursor, encode_cursor
from dps.jobs_models import SpecError
from dps.secrets import seed_fingerprint

#: Group names follow the connection-name rule (lower-case words joined by hyphens).
GROUP_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
_COLS = (
    "id, tenant_id, name, kid, seed_fingerprint, kcv, created_by, created_at, closed_at, "
    "last_check, last_check_at"
)
_MIN_LIMIT, _MAX_LIMIT = 1, 200


class JobGroup(BaseModel):
    """A ``dps_job_group`` row: identifiers of the caller-held key and seed, never the key."""

    model_config = ConfigDict(hide_input_in_errors=True)
    id: str
    tenant_id: str
    name: str
    kid: str
    seed_fingerprint: str
    #: the key check value (public; binds the DEK to the kid, F-13, migration 046); ``None`` for
    #: groups created before it — they keep the kid-only rule
    kcv: str | None = None
    created_by: str | None = None
    created_at: datetime
    closed_at: datetime | None = None
    #: the latest cross-system consistency check (M3, migration 020): counts and codes only
    last_check: dict[str, Any] | None = None
    last_check_at: datetime | None = None


def _row(row: Mapping[str, Any]) -> JobGroup:
    return JobGroup.model_validate(dict(row))


async def create_group(
    tenant_id: str, name: str, *, created_by: str | None
) -> tuple[JobGroup, KeyMaterial, SynthesisSeed]:
    """Create a group; the key material and seed are returned here and nowhere else.

    Raises:
        ValueError: ``name`` does not match :data:`GROUP_NAME_RE`.
        SpecError: ``group_name_taken`` (409) when the tenant already has a group of that name.
    """
    if not GROUP_NAME_RE.fullmatch(name):
        raise ValueError("group names must match ^[a-z0-9][a-z0-9-]{1,62}$")
    key = generate_key_material("key")
    seed = generate_synthesis_seed()
    group_id = new_id(PREFIX_GROUP)
    try:
        async with db.acquire(tenant_id) as conn, conn.transaction():
            await touch_tenant(tenant_id, conn=conn)
            row = await conn.fetchrow(
                f"INSERT INTO {db.schema_ident()}.dps_job_group (id, tenant_id, name, kid, "
                f"seed_fingerprint, kcv, created_by) VALUES ($1, $2, $3, $4, $5, $6, $7) "
                f"RETURNING {_COLS}",
                group_id,
                tenant_id,
                name,
                key.kid,
                seed_fingerprint(seed.seed),
                key.kcv or key_check_value(key),
                created_by,
            )
    except asyncpg.UniqueViolationError:
        raise SpecError("group_name_taken", 409, "a job group with this name exists") from None
    if row is None:  # RLS hides a row we just inserted only if the GUC binding is broken
        raise RuntimeError("created job group is not visible to its tenant")
    return _row(row), key, seed


async def get_group(tenant_id: str, group_id: str) -> JobGroup | None:
    """The tenant's group (RLS-scoped), or None."""
    async with db.acquire(tenant_id) as conn:
        row = await conn.fetchrow(
            f"SELECT {_COLS} FROM {db.schema_ident()}.dps_job_group "
            "WHERE tenant_id = $1 AND id = $2",
            tenant_id,
            group_id,
        )
    return _row(row) if row else None


async def save_last_check(tenant_id: str, group_id: str, check: Mapping[str, Any]) -> bool:
    """Store ``check`` (a ``ConsistencyCheckResult`` dumped as JSON: counts, codes, ids, paths)
    as the group's ``last_check``; returns whether the tenant's group was updated."""
    async with db.acquire(tenant_id) as conn:
        done = await conn.execute(
            f"UPDATE {db.schema_ident()}.dps_job_group SET last_check = $3::jsonb, "
            "last_check_at = now() WHERE tenant_id = $1 AND id = $2",
            tenant_id,
            group_id,
            dict(check),
        )
    return str(done).endswith(" 1")


async def list_groups(
    tenant_id: str, *, limit: int = 50, cursor: str | None = None
) -> tuple[list[JobGroup], str | None]:
    """Newest first; keyset pagination on ``(created_at, id)``. ``ValueError`` on a bad cursor."""
    params: list[Any] = [tenant_id]
    where = "tenant_id = $1"
    if cursor:
        ts, gid = decode_cursor(cursor)
        params += [ts, gid]
        where += " AND (created_at, id) < ($2::timestamptz, $3)"
    page = max(_MIN_LIMIT, min(_MAX_LIMIT, limit))
    async with db.acquire(tenant_id) as conn:
        rows = await conn.fetch(
            f"SELECT {_COLS} FROM {db.schema_ident()}.dps_job_group WHERE {where} "
            f"ORDER BY created_at DESC, id DESC LIMIT {page + 1}",
            *params,
        )
    items = [_row(r) for r in rows[:page]]
    nxt = encode_cursor(items[-1].created_at, items[-1].id) if len(rows) > page and items else None
    return items, nxt
