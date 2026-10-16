"""Per-call engine state: one API request, or one worker handler task (S3 review rounds 1 and 2).

A call gets its own

* custom entity registry (:class:`dps.engine.taxonomy.CustomEntities`, B3): the tiers its
  policies declare are never shared with another call, so no tenant can fill a registry another
  tenant needs, and
* CPU budget for policy-supplied patterns (:class:`dps.engine.detect.regex_guard.PatternBudget`,
  B1, R2-B1/R2-B2): every detector the call builds draws on it, so neither many detectors nor many
  leaves multiply the base allowance. An API request's budget has a ceiling
  (``DPS_PATTERN_CPU_SECONDS``); an NDJSON stream gives each item a budget of its own
  (:meth:`~dps.engine.detect.regex_guard.PatternBudget.for_stream`); a worker handler's allowance
  grows with what it scans, and every single run is capped.

Bound by the app's ``_CallScope`` middleware for every HTTP request and by the worker for every
handler task. A task copies its context when it is created, and ``asyncio.to_thread`` /
Starlette's threadpool copy it for every call, so the engine's worker threads share the call's
objects (they are thread-safe). Code outside a request or a job (tools, tests) runs without a
scope: the process-global ``TAXONOMY`` and a budget per detector apply there.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from dps.engine.detect.regex_guard import (
    DEFAULT_CPU_SECONDS,
    bind_pattern_budget,
    reset_pattern_budget,
)
from dps.engine.taxonomy import bind_custom_entities, reset_custom_entities


def bind_call_scope(*, pattern_run_cap: float = DEFAULT_CPU_SECONDS) -> None:
    """Bind a fresh scope for the rest of the current context (a worker handler's own task):
    no total pattern ceiling, each run capped at ``pattern_run_cap`` CPU seconds."""
    bind_custom_entities()
    bind_pattern_budget(run_cap=pattern_run_cap)


@contextmanager
def call_scope(*, pattern_ceiling: float | None = DEFAULT_CPU_SECONDS) -> Iterator[None]:
    """Bind a fresh scope for the ``with`` block (one HTTP request), then restore the previous
    one. ``pattern_ceiling`` caps the call's policy-pattern CPU in total and per run (``None``:
    no total ceiling, runs capped at the default)."""
    entities = bind_custom_entities()
    if pattern_ceiling is None:
        budget = bind_pattern_budget()
    else:
        budget = bind_pattern_budget(ceiling=pattern_ceiling, run_cap=pattern_ceiling)
    try:
        yield
    finally:
        reset_pattern_budget(budget)
        reset_custom_entities(entities)
