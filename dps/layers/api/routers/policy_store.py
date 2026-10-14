"""``/api/v1/policies`` — tenant policies, drafts, immutable versions, diffs (M5 Task 15, §9).

Registered **after** M0's policies router, so ``/policies/presets``, ``/validate``, ``/plan`` and
``/test-recognizer`` keep their meaning (those names are reserved policy names, migration 043).
Reads need ``detect``; changes need ``admin`` and are audited ``admin.policy.<verb>`` with counts
only (``version``, ``changes``) — never a policy body, a ticket or a note.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from dps.auth import require_scope
from dps.layers.api.deps import audit_action, principal_of, require_db, tenant_of
from dps.layers.api.errors import DpsHttpError, to_http
from dps.policy_store import (
    PolicyStoreError,
    create_policy,
    diff_versions,
    discard_draft,
    get_policy,
    get_version,
    list_policies,
    policy_yaml,
    publish,
    save_draft,
)
from dps.ratelimit import enforce_rate_limit

router = APIRouter(prefix="/api/v1/policies", tags=["policies"])
_READ = [Depends(require_scope("detect")), Depends(enforce_rate_limit), Depends(require_db)]
_ADMIN = [Depends(require_scope("admin")), Depends(enforce_rate_limit), Depends(require_db)]
_HIDDEN = ConfigDict(hide_input_in_errors=True, extra="forbid")


class PolicyCreate(BaseModel):
    """Body of ``POST /policies``: adopt a shipped preset under a tenant name."""

    model_config = _HIDDEN
    name: str = Field(max_length=63)
    base_preset: str = Field(max_length=64)


class DraftBody(BaseModel):
    """Body of ``PUT /policies/{name}/draft``: a full policy, or a preset plus overrides."""

    model_config = _HIDDEN
    body: dict[str, Any]


class PublishBody(BaseModel):
    """Body of ``POST /policies/{name}/publish``."""

    model_config = _HIDDEN
    change_ticket: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=1000)


def _mapped(exc: Exception) -> DpsHttpError:
    if isinstance(exc, PolicyStoreError):
        return DpsHttpError(exc.status, exc.code, exc.detail)
    return to_http(exc)


async def _read[T](body: Awaitable[T]) -> T:
    try:
        return await body
    except Exception as exc:  # noqa: BLE001 - mapped to a value-free {"detail", "code"}
        raise _mapped(exc) from None


async def _changed(
    request: Request,
    verb: str,
    body: Awaitable[tuple[dict[str, Any], dict[str, int]]],
    *,
    status: int,
) -> dict[str, Any]:
    """Await a mutation returning ``(answer, audit counts)``; audit ``admin.policy.<verb>`` as
    ``ok`` with the counts, or ``rejected``/``error`` with the mapped status, and raise it."""
    action = f"admin.policy.{verb}"
    try:
        result, counts = await body
    except Exception as exc:  # noqa: BLE001 - mapped to a value-free {"detail", "code"}
        err = _mapped(exc)
        audit_action(
            request,
            action=action,
            outcome="rejected" if err.status < 500 else "error",
            status=err.status,
        )
        raise err from None
    audit_action(request, action=action, status=status, counts=counts)
    return result


def _actor(request: Request) -> str:
    return principal_of(request).key_id


@router.get("", dependencies=_READ)
async def list_(request: Request) -> dict[str, Any]:
    """The shipped presets and the tenant's own policies."""
    presets, tenant = await _read(list_policies(tenant_of(request)))
    return {
        "presets": [p.model_dump(mode="json") for p in presets],
        "tenant": [t.model_dump(mode="json") for t in tenant],
    }


@router.post("", status_code=201, dependencies=_ADMIN)
async def create(body: PolicyCreate, request: Request) -> dict[str, Any]:
    """Adopt a preset: version 1 is the preset itself (note "shipped preset")."""
    tenant = tenant_of(request)

    async def run() -> tuple[dict[str, Any], dict[str, int]]:
        detail = await create_policy(tenant, body.name, body.base_preset, actor=_actor(request))
        return detail.model_dump(mode="json"), {"version": 1, "changes": 0}

    return await _changed(request, "create", run(), status=201)


@router.get("/{name}", dependencies=_READ)
async def get(name: str, request: Request) -> dict[str, Any]:
    """One tenant policy: active and draft bodies and every version."""
    return (await _read(get_policy(tenant_of(request), name))).model_dump(mode="json")


@router.put("/{name}/draft", dependencies=_ADMIN)
async def put_draft(name: str, body: DraftBody, request: Request) -> dict[str, Any]:
    """Validate and keep a draft (400 ``invalid_policy`` names the broken rule)."""
    tenant = tenant_of(request)

    async def run() -> tuple[dict[str, Any], dict[str, int]]:
        detail = await save_draft(tenant, name, body.body, actor=_actor(request))
        counts = {"version": detail.active_version or 0, "changes": detail.draft_changes}
        return detail.model_dump(mode="json"), counts

    return await _changed(request, "draft", run(), status=200)


@router.delete("/{name}/draft", dependencies=_ADMIN)
async def delete_draft(name: str, request: Request) -> dict[str, Any]:
    """Discard the pending draft."""
    tenant = tenant_of(request)

    async def run() -> tuple[dict[str, Any], dict[str, int]]:
        detail = await discard_draft(tenant, name)
        return detail.model_dump(mode="json"), {"version": detail.active_version or 0}

    return await _changed(request, "discard", run(), status=200)


@router.post("/{name}/publish", status_code=201, dependencies=_ADMIN)
async def publish_(
    name: str, request: Request, body: Annotated[PublishBody | None, Body()] = None
) -> dict[str, Any]:
    """Publish the draft as the next immutable version and make it active."""
    tenant = tenant_of(request)
    settings = request.app.state.settings
    payload = body or PublishBody()

    async def run() -> tuple[dict[str, Any], dict[str, int]]:
        before = await get_policy(tenant, name)
        version = await publish(
            tenant,
            name,
            change_ticket=payload.change_ticket,
            note=payload.note,
            actor=_actor(request),
            settings=settings,
        )
        counts = {"version": version.version, "changes": before.draft_changes}
        return version.model_dump(mode="json"), counts

    return await _changed(request, "publish", run(), status=201)


@router.get("/{name}/versions/{version}", dependencies=_READ, response_model=None)
async def version_(
    name: str,
    version: int,
    request: Request,
    format: Literal["json", "yaml"] = "json",  # noqa: A002 - the query parameter's name
) -> dict[str, Any] | Response:
    """One published body as JSON, or a ``text/yaml`` attachment."""
    body = await _read(get_version(tenant_of(request), name, version))
    if format == "json":
        return body
    return Response(
        policy_yaml(body),
        media_type="text/yaml",
        headers={"Content-Disposition": f'attachment; filename="{name}-v{version}.yaml"'},
    )


@router.get("/{name}/diff", dependencies=_READ)
async def diff(
    name: str,
    request: Request,
    from_: Annotated[int, Query(alias="from", ge=1)],
    to: Annotated[str, Query(pattern=r"^(draft|[1-9][0-9]{0,5})$")],
) -> dict[str, Any]:
    """Leaf changes from version ``from`` to version ``to`` (or ``draft``)."""
    target: int | Literal["draft"] = "draft" if to == "draft" else int(to)
    changes = await _read(diff_versions(tenant_of(request), name, from_, target))
    return {"from": from_, "to": target, "changes": [c.model_dump(mode="json") for c in changes]}
