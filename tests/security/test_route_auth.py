"""S6: every ``/api/v1`` route declares a scope, and every one refuses an anonymous call.

``require_scope`` / ``require_any_scope`` tag their dependency (``__dps_scope__``); this walk
fails for any route a milestone adds without one (fix the route, never :data:`PUBLIC`). FastAPI
keeps included routers as nested containers, so the walk uses ``iter_route_contexts`` (the
effective routes with their router-level dependencies), not ``app.routes``.
"""

from __future__ import annotations

import re

from fastapi import FastAPI
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts
from fastapi.testclient import TestClient

from dps.app import create_app
from dps.config import Settings

PUBLIC = {
    "/health",
    "/readyz",
    "/metrics",
    "/",
    "/{full_path:path}",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
    "/api/v1/openapi.json",
}


def _app() -> FastAPI:
    return create_app(
        Settings(
            _env_file=None,
            env="local",
            auth_enabled=True,
            api_keys="",
            bootstrap_api_key="",
            llm_provider="stub",
            rate_limit_enabled=False,
        )
    )


def _api_routes(app: FastAPI) -> list[RouteContext]:
    return [
        ctx
        for ctx in iter_route_contexts(app.routes)
        if isinstance(ctx.original_route, APIRoute) and ctx.path not in PUBLIC
    ]


def _scoped(ctx: RouteContext) -> bool:
    return any(getattr(d.call, "__dps_scope__", None) for d in ctx.dependant.dependencies)


def test_the_walk_sees_the_included_routers() -> None:
    paths = {ctx.path for ctx in _api_routes(_app())}
    assert len(paths) > 40  # the plan's app.routes walk saw 2 here (FastAPI 0.143)
    assert {"/api/v1/jobs/run", "/api/v1/admin/keys", "/api/v1/obfuscate"} <= paths


def test_every_api_route_declares_a_scope() -> None:
    routes = _api_routes(_app())
    unscoped = sorted(f"{sorted(c.methods or ())} {c.path}" for c in routes if not _scoped(c))
    assert unscoped == []
    assert all((c.path or "").startswith("/api/v1/") for c in routes)


def test_every_api_route_refuses_an_anonymous_call() -> None:
    app = _app()
    client = TestClient(app)
    for ctx in _api_routes(app):
        path = re.sub(r"\{[^}]+\}", "x", ctx.path or "")
        for method in sorted((ctx.methods or set()) - {"HEAD", "OPTIONS"}):
            status = client.request(method, path, json={}).status_code
            assert status == 401, f"{method} {ctx.path} answered {status} without a key"
