"""Tenant policies (M5 Task 15, mockup 05): drafts, immutable published versions, diffs and
``@name[:vN]`` references (contract §5.5).

A tenant adopts a shipped preset under a name (version 1 is the preset itself), edits a draft and
publishes it as the next version. Published versions are never changed or deleted (a trigger
refuses both, migration 043). A request names a stored policy as ``@<name>`` (the active
version) or ``@<name>:v<N>`` (a pinned version); :func:`resolve_ref` turns the reference into the
stored body **at accept time**, so jobs record exactly what they ran.

Policies are configuration, never customer data. Errors (:class:`PolicyStoreError`) carry a
status, a stable code and a detail that names fields, methods and rules — never a value.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

import asyncpg
import yaml
from pydantic import BaseModel

from dps import db
from dps.config import Settings
from dps.engine.policy import (
    Policy,
    load_preset,
    preset_names,
    preset_summaries,
    resolve_policy,
)

__all__ = [
    "REF",
    "RESERVED_NAMES",
    "PolicyChange",
    "PolicyStoreError",
    "PolicyVersionView",
    "PresetView",
    "TenantPolicyDetail",
    "TenantPolicyView",
    "active_versions",
    "create_policy",
    "diff_policies",
    "diff_versions",
    "discard_draft",
    "flatten_policy",
    "get_policy",
    "get_version",
    "list_policies",
    "policy_yaml",
    "publish",
    "resolve_ref",
    "save_draft",
]

#: ``@name`` (the active version) or ``@name:vN`` (a pinned version).
REF = re.compile(r"^@([a-z0-9][a-z0-9-]{1,62})(?::v([0-9]{1,6}))?$")
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
#: Names that would shadow M0's ``/policies/<word>`` routes (also refused by 043's CHECK).
RESERVED_NAMES = frozenset({"presets", "validate", "plan", "test-recognizer"})
SHIPPED_NOTE = "shipped preset"


class PolicyStoreError(Exception):
    """A policy-store operation is refused (status + code + value-free detail)."""

    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status, self.code, self.detail = status, code, detail


class PolicyChange(BaseModel):
    """One leaf that differs between two policy bodies (dotted path, see :func:`flatten_policy`)."""

    op: Literal["add", "remove", "change"]
    path: str
    before: Any = None
    after: Any = None


class PresetView(BaseModel):
    """A shipped preset as the Policies page lists it."""

    name: str
    version: int
    hash: str
    description: str
    mode: Literal["obfuscate", "synthesize"]
    reversible: bool
    jobs: int


class PolicyVersionView(BaseModel):
    """One published version (the body is fetched separately)."""

    version: int
    hash: str
    change_ticket: str | None
    note: str | None
    published_by: str | None
    published_at: datetime
    jobs_pinned: int


class TenantPolicyView(BaseModel):
    """A tenant policy in the list: the active version and whether a draft is pending."""

    name: str
    base_preset: str | None
    active_version: int | None
    active_hash: str | None
    draft: bool
    draft_changes: int
    published_at: datetime | None
    published_by: str | None
    jobs: int


class TenantPolicyDetail(TenantPolicyView):
    """A tenant policy with its active and draft bodies and every version (newest first)."""

    active_body: dict[str, Any] | None
    draft_body: dict[str, Any] | None
    draft_by: str | None
    draft_updated_at: datetime | None
    versions: list[PolicyVersionView]


# --- pure helpers ---------------------------------------------------------------------------


def flatten_policy(body: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """Dotted leaves; list items keyed by their ``name`` or ``path`` when they have one."""
    out: dict[str, Any] = {}
    for key in sorted(body):
        value, path = body[key], f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            out.update(flatten_policy(value, path))
        elif isinstance(value, list) and value and all(isinstance(v, Mapping) for v in value):
            for index, item in enumerate(value):
                label = item.get("name") or item.get("path") or index
                out.update(flatten_policy(item, f"{path}[{label}]"))
        else:
            out[path] = value
    return out


def diff_policies(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[PolicyChange]:
    """Leaf changes from ``before`` to ``after`` in path order (``name``/``version`` ignored)."""
    a, b = flatten_policy(before), flatten_policy(after)
    changes: list[PolicyChange] = []
    for path in sorted(set(a) | set(b)):
        if path in ("version", "name"):
            continue
        if path not in a:
            changes.append(PolicyChange(op="add", path=path, after=b[path]))
        elif path not in b:
            changes.append(PolicyChange(op="remove", path=path, before=a[path]))
        elif a[path] != b[path]:
            changes.append(PolicyChange(op="change", path=path, before=a[path], after=b[path]))
    return changes


def _if_keys(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {("if" if k == "if_" else k): _if_keys(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_if_keys(v) for v in value]
    return value


def policy_yaml(body: Mapping[str, Any]) -> str:
    """The body as YAML (sorted keys); a ``when`` rule's ``if_`` is written back as ``if``."""
    return yaml.safe_dump(_if_keys(dict(body)), sort_keys=True, allow_unicode=False)


def _check_name(name: str) -> None:
    if not _NAME.fullmatch(name) or name in RESERVED_NAMES:
        raise PolicyStoreError(
            422,
            "invalid_name",
            "a policy name is 2-63 characters of a-z, 0-9 and '-' (starting with a letter or "
            "digit) and not one of presets, validate, plan, test-recognizer",
        )


def _validated(body: Mapping[str, Any], *, name: str, version: int) -> Policy:
    """``body`` (a full policy or ``{"preset", ...overrides}``) as a Policy named ``name``."""
    try:
        policy = resolve_policy(dict(body), default_name=name)
        return Policy.model_validate(
            {**policy.model_dump(mode="json"), "name": name, "version": version}
        )
    except KeyError as exc:  # an unknown preset: the message names a preset, never data
        raise PolicyStoreError(400, "invalid_policy", f"invalid policy: {exc.args[0]}") from None
    except ValueError as exc:  # pydantic: fields and rules (hide_input_in_errors), never values
        raise PolicyStoreError(400, "invalid_policy", f"invalid policy: {exc}") from None


# --- database -------------------------------------------------------------------------------


def _s() -> str:
    return db.schema_ident()


async def _job_counts(
    conn: Any, tenant_id: str
) -> tuple[dict[str, int], dict[tuple[str, int], int]]:
    """Run jobs per preset name (string policies) and per stored ``(name, version)``."""
    rows = await conn.fetch(
        "SELECT CASE WHEN jsonb_typeof(spec->'policy') = 'string' THEN spec->>'policy' "
        "ELSE spec->'policy'->>'name' END AS name, "
        "CASE WHEN jsonb_typeof(spec->'policy'->'version') = 'number' "
        "THEN (spec->'policy'->>'version')::numeric::int END AS version, count(*)::int AS n "
        f"FROM {_s()}.dps_job WHERE tenant_id = $1 AND kind = 'run' "
        "AND jsonb_typeof(spec->'policy') IN ('string', 'object') GROUP BY 1, 2",
        tenant_id,
    )
    presets: dict[str, int] = {}
    stored: dict[tuple[str, int], int] = {}
    for r in rows:
        if r["name"] is None:
            continue
        if r["version"] is None:
            presets[r["name"]] = presets.get(r["name"], 0) + int(r["n"])
        else:
            key = (str(r["name"]), int(r["version"]))
            stored[key] = stored.get(key, 0) + int(r["n"])
    return presets, stored


def _version_view(r: Any, jobs: int) -> PolicyVersionView:
    return PolicyVersionView(
        version=int(r["version"]),
        hash=r["policy_hash"],
        change_ticket=r["change_ticket"],
        note=r["note"],
        published_by=r["published_by"],
        published_at=r["published_at"],
        jobs_pinned=jobs,
    )


async def _detail(conn: Any, tenant_id: str, name: str) -> TenantPolicyDetail:
    row = await conn.fetchrow(
        f"SELECT * FROM {_s()}.dps_policy WHERE tenant_id = $1 AND name = $2", tenant_id, name
    )
    if row is None:
        raise PolicyStoreError(404, "policy_not_found", "no such policy")
    versions = await conn.fetch(
        "SELECT version, body, policy_hash, change_ticket, note, published_by, published_at "
        f"FROM {_s()}.dps_policy_version WHERE tenant_id = $1 AND name = $2 ORDER BY version DESC",
        tenant_id,
        name,
    )
    _, stored = await _job_counts(conn, tenant_id)
    active = next((v for v in versions if v["version"] == row["active_version"]), None)
    active_body = dict(active["body"]) if active is not None else None
    draft = dict(row["draft"]) if row["draft"] is not None else None
    return TenantPolicyDetail(
        name=name,
        base_preset=row["base_preset"],
        active_version=row["active_version"],
        active_hash=active["policy_hash"] if active is not None else None,
        draft=draft is not None,
        draft_changes=len(diff_policies(active_body or {}, draft)) if draft is not None else 0,
        published_at=active["published_at"] if active is not None else None,
        published_by=active["published_by"] if active is not None else None,
        jobs=sum(n for (pname, _), n in stored.items() if pname == name),
        active_body=active_body,
        draft_body=draft,
        draft_by=row["draft_by"],
        draft_updated_at=row["draft_updated_at"],
        versions=[_version_view(v, stored.get((name, int(v["version"])), 0)) for v in versions],
    )


def _view(detail: TenantPolicyDetail) -> TenantPolicyView:
    return TenantPolicyView.model_validate(
        detail.model_dump(include=set(TenantPolicyView.model_fields))
    )


async def list_policies(tenant_id: str) -> tuple[list[PresetView], list[TenantPolicyView]]:
    """The shipped presets (with this tenant's job counts) and the tenant's own policies."""
    async with db.acquire(tenant_id) as conn:
        presets_jobs, _ = await _job_counts(conn, tenant_id)
        names = await conn.fetch(
            f"SELECT name FROM {_s()}.dps_policy WHERE tenant_id = $1 ORDER BY name", tenant_id
        )
        details = [await _detail(conn, tenant_id, r["name"]) for r in names]
    presets = [
        PresetView(
            name=p["name"],
            version=int(p["version"]),
            hash=p["hash"],
            description=p["description"],
            mode=p["mode"],
            reversible=bool(p["reversible"]),
            jobs=presets_jobs.get(p["name"], 0),
        )
        for p in preset_summaries()
    ]
    return presets, [_view(d) for d in details]


async def create_policy(
    tenant_id: str, name: str, base_preset: str, *, actor: str
) -> TenantPolicyDetail:
    """Adopt ``base_preset`` as the tenant policy ``name``; version 1 (note "shipped preset") is
    the preset itself, published at once.

    Raises:
        PolicyStoreError: 422 ``invalid_name`` / ``invalid_preset``; 409 ``policy_exists``.
    """
    _check_name(name)
    if base_preset not in preset_names():
        raise PolicyStoreError(422, "invalid_preset", "base_preset must name a shipped preset")
    policy = Policy.model_validate(
        {**load_preset(base_preset).model_dump(mode="json"), "name": name, "version": 1}
    )
    body = policy.model_dump(mode="json")
    s = _s()
    async with db.acquire(tenant_id) as conn:
        try:
            async with conn.transaction():
                await conn.execute(
                    f"INSERT INTO {s}.dps_policy (tenant_id, name, base_preset, active_version, "
                    "created_by) VALUES ($1, $2, $3, 1, $4)",
                    tenant_id,
                    name,
                    base_preset,
                    actor,
                )
                await conn.execute(
                    f"INSERT INTO {s}.dps_policy_version (tenant_id, name, version, body, "
                    "policy_hash, note, published_by) VALUES ($1, $2, 1, $3, $4, $5, $6)",
                    tenant_id,
                    name,
                    body,
                    policy.config_hash(),
                    SHIPPED_NOTE,
                    actor,
                )
        except asyncpg.UniqueViolationError:
            raise PolicyStoreError(409, "policy_exists", "a policy of this name exists") from None
        return await _detail(conn, tenant_id, name)


async def get_policy(tenant_id: str, name: str) -> TenantPolicyDetail:
    """One tenant policy (404 ``policy_not_found``)."""
    async with db.acquire(tenant_id) as conn:
        return await _detail(conn, tenant_id, name)


async def save_draft(
    tenant_id: str, name: str, body: Mapping[str, Any], *, actor: str
) -> TenantPolicyDetail:
    """Validate ``body`` (a full policy, or a preset plus overrides) and keep it as the draft.

    Raises:
        PolicyStoreError: 404 ``policy_not_found``; 400 ``invalid_policy`` (nothing written).
    """
    draft = _validated(body, name=name, version=1).model_dump(mode="json")
    async with db.acquire(tenant_id) as conn:
        status = await conn.execute(
            f"UPDATE {_s()}.dps_policy SET draft = $3, draft_by = $4, draft_updated_at = now(), "
            "updated_at = now() WHERE tenant_id = $1 AND name = $2",
            tenant_id,
            name,
            draft,
            actor,
        )
        if db.rowcount(status) == 0:
            raise PolicyStoreError(404, "policy_not_found", "no such policy")
        return await _detail(conn, tenant_id, name)


async def discard_draft(tenant_id: str, name: str) -> TenantPolicyDetail:
    """Drop the pending draft (a no-op without one); 404 ``policy_not_found``."""
    async with db.acquire(tenant_id) as conn:
        status = await conn.execute(
            f"UPDATE {_s()}.dps_policy SET draft = NULL, draft_by = NULL, draft_updated_at = NULL, "
            "updated_at = now() WHERE tenant_id = $1 AND name = $2",
            tenant_id,
            name,
        )
        if db.rowcount(status) == 0:
            raise PolicyStoreError(404, "policy_not_found", "no such policy")
        return await _detail(conn, tenant_id, name)


def _check_text(value: str | None, *, low: int, high: int, code: str, label: str) -> None:
    if value is not None and not low <= len(value) <= high:
        raise PolicyStoreError(422, code, f"{label} must be {low}-{high} characters")


async def publish(
    tenant_id: str,
    name: str,
    *,
    change_ticket: str | None,
    note: str | None,
    actor: str,
    settings: Settings | None = None,
) -> PolicyVersionView:
    """Publish the draft as the next version and make it active (one transaction).

    A body whose ``verify.leakage_check`` is ``warn`` is refused while
    ``DPS_VERIFY_WARN_ALLOWED`` is false (spec §10 strict verification).

    Raises:
        PolicyStoreError: 404 ``policy_not_found``; 409 ``no_draft``; 422
            ``verify_warn_disallowed`` / ``invalid_change_ticket`` / ``invalid_note``.
    """
    settings = settings or db.active_settings()
    _check_text(change_ticket, low=3, high=64, code="invalid_change_ticket", label="change_ticket")
    _check_text(note, low=0, high=1000, code="invalid_note", label="note")
    s = _s()
    async with db.acquire(tenant_id) as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                f"SELECT draft FROM {s}.dps_policy WHERE tenant_id = $1 AND name = $2 FOR UPDATE",
                tenant_id,
                name,
            )
            if row is None:
                raise PolicyStoreError(404, "policy_not_found", "no such policy")
            if row["draft"] is None:
                raise PolicyStoreError(409, "no_draft", "the policy has no draft to publish")
            version = int(
                await conn.fetchval(
                    f"SELECT coalesce(max(version), 0) + 1 FROM {s}.dps_policy_version "
                    "WHERE tenant_id = $1 AND name = $2",
                    tenant_id,
                    name,
                )
            )
            policy = _validated(dict(row["draft"]), name=name, version=version)
            if policy.verify.leakage_check == "warn" and not settings.verify_warn_allowed:
                raise PolicyStoreError(
                    422,
                    "verify_warn_disallowed",
                    "verify.leakage_check=warn is not allowed on this deployment",
                )
            vrow = await conn.fetchrow(
                f"INSERT INTO {s}.dps_policy_version (tenant_id, name, version, body, "
                "policy_hash, change_ticket, note, published_by) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
                "RETURNING version, policy_hash, change_ticket, note, published_by, published_at",
                tenant_id,
                name,
                version,
                policy.model_dump(mode="json"),
                policy.config_hash(),
                change_ticket,
                note,
                actor,
            )
            await conn.execute(
                f"UPDATE {s}.dps_policy SET active_version = $3, draft = NULL, draft_by = NULL, "
                "draft_updated_at = NULL, updated_at = now() WHERE tenant_id = $1 AND name = $2",
                tenant_id,
                name,
                version,
            )
    return _version_view(vrow, 0)


async def _version_body(
    conn: Any, tenant_id: str, name: str, version: int
) -> dict[str, Any] | None:
    body = await conn.fetchval(
        f"SELECT body FROM {_s()}.dps_policy_version WHERE tenant_id = $1 AND name = $2 "
        "AND version = $3",
        tenant_id,
        name,
        version,
    )
    return dict(body) if body is not None else None


async def get_version(tenant_id: str, name: str, version: int) -> dict[str, Any]:
    """One published body; 404 ``policy_not_found`` / ``version_not_found``."""
    async with db.acquire(tenant_id) as conn:
        exists = await conn.fetchval(
            f"SELECT 1 FROM {_s()}.dps_policy WHERE tenant_id = $1 AND name = $2", tenant_id, name
        )
        if exists is None:
            raise PolicyStoreError(404, "policy_not_found", "no such policy")
        body = await _version_body(conn, tenant_id, name, version)
    if body is None:
        raise PolicyStoreError(404, "version_not_found", "no such version")
    return body


async def diff_versions(
    tenant_id: str, name: str, from_version: int, to: int | Literal["draft"]
) -> list[PolicyChange]:
    """Changes from a published version to another one or to the draft (409 ``no_draft``)."""
    before = await get_version(tenant_id, name, from_version)
    if to == "draft":
        detail = await get_policy(tenant_id, name)
        if detail.draft_body is None:
            raise PolicyStoreError(409, "no_draft", "the policy has no draft")
        return diff_policies(before, detail.draft_body)
    return diff_policies(before, await get_version(tenant_id, name, to))


async def resolve_ref(
    ref: str | dict[str, Any] | None, tenant_id: str
) -> str | dict[str, Any] | None:
    """``@name`` → the active body, ``@name:vN`` → that version's body; anything else unchanged.

    Raises:
        PolicyStoreError: 400 ``invalid_policy_ref`` (malformed), 404 ``policy_not_found``
            (no such policy or version for this tenant), 409 ``policy_not_published``.
    """
    if not (isinstance(ref, str) and ref.startswith("@")):
        return ref
    match = REF.fullmatch(ref)
    if match is None:
        raise PolicyStoreError(
            400, "invalid_policy_ref", "a policy reference is @<name> or @<name>:v<version>"
        )
    name, pinned = match.group(1), match.group(2)
    async with db.acquire(tenant_id) as conn:
        active = await conn.fetchrow(
            f"SELECT active_version FROM {_s()}.dps_policy WHERE tenant_id = $1 AND name = $2",
            tenant_id,
            name,
        )
        if active is None:
            raise PolicyStoreError(404, "policy_not_found", "no such policy")
        if pinned is None and active["active_version"] is None:
            raise PolicyStoreError(
                409, "policy_not_published", "the policy has no published version"
            )
        version = int(pinned) if pinned is not None else int(active["active_version"])
        body = await _version_body(conn, tenant_id, name, version)
    if body is None:
        raise PolicyStoreError(404, "policy_not_found", "no such policy version")
    return body


async def active_versions(tenant_id: str) -> dict[str, int]:
    """``{name: active version}`` of the tenant's published policies (posture, reports)."""
    async with db.acquire(tenant_id) as conn:
        rows = await conn.fetch(
            f"SELECT name, active_version FROM {_s()}.dps_policy "
            "WHERE tenant_id = $1 AND active_version IS NOT NULL",
            tenant_id,
        )
    return {str(r["name"]): int(r["active_version"]) for r in rows}
