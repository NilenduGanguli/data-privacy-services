"""Per-call engine state: one API request, or one worker handler task (S3 review round 1).

A call gets its own

* custom entity registry (:class:`dps.engine.taxonomy.CustomEntities`, B3): the tiers its
  policies declare are never shared with another call, so no tenant can fill a registry another
  tenant needs, and
* CPU budget for policy-supplied patterns (:class:`dps.engine.detect.regex_guard.PatternBudget`,
  B1): every detector the call builds draws on it, so neither many detectors (NDJSON items) nor
  many leaves multiply the base allowance.

Bound by the app's ``_CallScope`` middleware for every HTTP request and by the worker for every
handler task. A task copies its context when it is created, and ``asyncio.to_thread`` /
Starlette's threadpool copy it for every call, so the engine's worker threads share the call's
objects (they are thread-safe). Code outside a request or a job (tools, tests) runs without a
scope: the process-global ``TAXONOMY`` and a budget per detector apply there.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from dps.engine.detect.regex_guard import bind_pattern_budget, reset_pattern_budget
from dps.engine.taxonomy import bind_custom_entities, reset_custom_entities


def bind_call_scope() -> None:
    """Bind a fresh scope for the rest of the current context (a worker handler's own task)."""
    bind_custom_entities()
    bind_pattern_budget()


@contextmanager
def call_scope() -> Iterator[None]:
    """Bind a fresh scope for the ``with`` block (one HTTP request), then restore the previous
    one."""
    entities = bind_custom_entities()
    budget = bind_pattern_budget()
    try:
        yield
    finally:
        reset_pattern_budget(budget)
        reset_custom_entities(entities)
