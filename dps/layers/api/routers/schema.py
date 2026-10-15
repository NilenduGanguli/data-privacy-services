"""POST /api/v1/schema/generator-spec and /api/v1/schema/synthesize (spec §5.6, contract §9).

Profiling (DDL parsing, detection over a sample), execution and the sample's leakage sweep are
CPU-bound and run in worker threads. With a sample, the executor steers around any generated value
that holds one of the sample's sensitive values (``schema_profile.sample_guard``), and an ID-like
key around its own sampled values (``schema_profile.sample_ids``; the draws they discarded are the
report's ``sample_redraws``), and the sweep fails closed on what is left: when
a generated row holds a value of a sample field DPS judged sensitive, nothing is returned (422
``leakage``). Size limits are coded: 413 ``ddl_too_large`` / ``sample_too_large`` /
``too_many_rows``. ``/schema/synthesize`` honours ``Idempotency-Key`` (contract §9); a replay is
identical only with the caller's ``seed`` (and, for a sample, the same sample: replaying the
returned spec and seed alone gives the same rows except where ``sample_redraws`` steered).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from dps.auth import require_scope
from dps.engine.detect.ensemble import Detector, DetectorConfig
from dps.engine.synth.schema_gen import (
    GeneratorSpec,
    SchemaError,
    SchemaResult,
    execute_spec,
    rows_to_csv,
)
from dps.engine.synth.schema_profile import (
    MAX_SAMPLE_ROWS,
    ProfileResult,
    StructureProfile,
    profile_from_ddl,
    profile_from_json_schema,
    profile_from_sample,
    propose_spec,
    sample_guard,
    sample_ids,
    verify_against_sample,
)
from dps.engine.synth.surrogates import (
    SynthesisSeed,
    generate_synthesis_seed,
    seed_bytes,
    seed_from_caller,
)
from dps.engine.verify import Verification
from dps.layers.api.common import run_engine, with_idempotency
from dps.ratelimit import enforce_rate_limit

router = APIRouter(prefix="/api/v1/schema", tags=["schema"])


class SchemaSource(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, hide_input_in_errors=True)
    json_schema: dict[str, Any] | None = Field(default=None, alias="schema")
    # sizes are checked by the profilers, so they answer with coded 413s (ddl_too_large,
    # sample_too_large) rather than a generic validation error
    ddl: str | None = None
    sample: list[dict[str, Any]] | None = None
    name: str = Field(default="dataset", pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    locale: str | None = None
    use_llm: bool = True


class GeneratorSpecRequest(SchemaSource):
    pass


class SchemaSynthesizeRequest(SchemaSource):
    spec: GeneratorSpec | None = None
    rows: int = Field(default=100, ge=1)
    seed: dict[str, Any] | None = None
    format: Literal["json", "csv"] = "json"


class LlmUsage(BaseModel):
    calls: int = 0
    raw_pii_sent: int = 0


class GeneratorSpecOut(BaseModel):
    spec: GeneratorSpec
    spec_hash: str
    profile: StructureProfile
    source: str
    fallback: str | None = None
    llm: LlmUsage
    trace_id: str


class SchemaReport(BaseModel):
    mode: str = "schema.synthesize"
    rows: int
    fields: int
    generators_used: dict[str, int]
    nulls: int
    unique_retries: int
    sample_redraws: int = 0
    spec_hash: str
    source: str
    fallback: str | None = None
    llm: LlmUsage
    verification: Verification | None = None
    duration_ms: float


class SchemaSynthesizeOut(BaseModel):
    rows: list[dict[str, Any]] | None = None
    csv: str | None = None
    spec: GeneratorSpec
    spec_hash: str
    synthesis_seed: SynthesisSeed
    report: SchemaReport
    trace_id: str = ""


def _exactly_one(
    body: SchemaSource, spec: GeneratorSpec | None = None, *, allow_spec: bool = False
) -> None:
    given = sum(x is not None for x in (body.json_schema, body.ddl, body.sample, spec))
    if given != 1:
        options = "spec, schema, ddl or sample" if allow_spec else "schema, ddl or sample"
        raise SchemaError("exactly_one_input", f"give exactly one of {options}")


def _execute(
    spec: GeneratorSpec, rows: int, seed: bytes, sample: ProfileResult | None
) -> SchemaResult:
    """Execution, in a worker thread. With a sample the executor steers around the sample's
    sensitive values, and an ID-like key around its own sampled values; the guards are built here
    too (they normalise every sampled value)."""
    if sample is None:
        return execute_spec(spec, rows=rows, seed=seed)
    return execute_spec(
        spec, rows=rows, seed=seed, avoid=sample_guard(sample), avoid_in=sample_ids(sample)
    )


async def _profile(body: SchemaSource, request: Request) -> ProfileResult:
    settings, engine = request.app.state.settings, request.app.state.engine
    if body.json_schema is not None:
        return await asyncio.to_thread(profile_from_json_schema, body.json_schema, name=body.name)
    if body.ddl is not None:
        name = None if body.name == "dataset" else body.name
        return await asyncio.to_thread(profile_from_ddl, body.ddl, name=name)
    assert body.sample is not None
    if len(body.sample) > MAX_SAMPLE_ROWS:
        raise SchemaError(
            "sample_too_large", f"samples are limited to {MAX_SAMPLE_ROWS} rows", status=413
        )
    if sum(len(row) for row in body.sample) > settings.max_items:
        raise SchemaError(
            "sample_too_large", f"samples are limited to {settings.max_items} values", status=413
        )
    detector = Detector(
        DetectorConfig(
            min_score=settings.min_score,
            locale=body.locale or settings.default_locale,
            ner_enabled=engine.ner is not None,
        ),
        ner=engine.ner,
    )
    return await asyncio.to_thread(profile_from_sample, body.sample, detector, name=body.name)


@router.post(
    "/generator-spec",
    response_model=GeneratorSpecOut,
    dependencies=[Depends(require_scope("synthesize")), Depends(enforce_rate_limit)],
)
async def generator_spec(body: GeneratorSpecRequest, request: Request) -> GeneratorSpecOut:
    settings, engine = request.app.state.settings, request.app.state.engine

    async def call() -> tuple[GeneratorSpec, ProfileResult, dict[str, Any]]:
        _exactly_one(body)
        result = await _profile(body, request)
        spec, info = await propose_spec(
            result,
            engine.gateway,
            locale=body.locale or settings.default_locale,
            use_llm=body.use_llm,
        )
        return spec, result, info

    (spec, result, info), trace_id = await run_engine(
        request, mode="schema.generator-spec", policy_name="-", call=call
    )
    return GeneratorSpecOut(
        spec=spec,
        spec_hash=spec.spec_hash(),
        profile=result.profile,
        source=info["source"],
        fallback=info["fallback"],
        llm=LlmUsage(calls=info["llm_calls"]),
        trace_id=trace_id,
    )


@router.post(
    "/synthesize",
    response_model=SchemaSynthesizeOut,
    dependencies=[Depends(require_scope("synthesize")), Depends(enforce_rate_limit)],
)
async def schema_synthesize(body: SchemaSynthesizeRequest, request: Request) -> SchemaSynthesizeOut:
    settings, engine = request.app.state.settings, request.app.state.engine

    async def call() -> SchemaSynthesizeOut:
        started = time.perf_counter()
        _exactly_one(body, body.spec, allow_spec=True)
        if body.rows > settings.schema_max_rows:
            raise SchemaError(
                "too_many_rows", f"at most {settings.schema_max_rows} rows per request", status=413
            )
        result: ProfileResult | None = None
        info: dict[str, Any]
        if body.spec is not None:
            spec, info = body.spec, {"llm_calls": 0, "source": "caller", "fallback": None}
        else:
            result = await _profile(body, request)
            spec, info = await propose_spec(
                result,
                engine.gateway,
                locale=body.locale or settings.default_locale,
                use_llm=body.use_llm,
            )
        try:
            seed = (
                seed_from_caller(body.seed) if body.seed is not None else generate_synthesis_seed()
            )
        except ValueError:
            raise SchemaError("invalid_seed", "the synthesis seed is not valid") from None
        sample = result if result is not None and result.profile.source == "sample" else None
        generated = await asyncio.to_thread(_execute, spec, body.rows, seed_bytes(seed), sample)
        verification: Verification | None = None
        if sample is not None:
            verification = await asyncio.to_thread(verify_against_sample, generated.rows, sample)
            if not verification.passed:
                raise SchemaError(
                    "leakage", "generated rows matched a sample value; nothing was returned"
                )
        report = SchemaReport(
            rows=len(generated.rows),
            fields=len(spec.fields),
            generators_used=generated.generators_used,
            nulls=generated.nulls,
            unique_retries=generated.unique_retries,
            sample_redraws=generated.sample_redraws,
            spec_hash=generated.spec_hash,
            source=info["source"],
            fallback=info["fallback"],
            llm=LlmUsage(calls=info["llm_calls"]),
            verification=verification,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        csv_text = rows_to_csv(generated.rows, spec) if body.format == "csv" else None
        return SchemaSynthesizeOut(
            rows=None if csv_text is not None else generated.rows,
            csv=csv_text,
            spec=spec,
            spec_hash=generated.spec_hash,
            synthesis_seed=seed,
            report=report,
        )

    async def run() -> SchemaSynthesizeOut:
        out, trace_id = await run_engine(
            request, mode="schema.synthesize", policy_name="-", call=call
        )
        return out.model_copy(update={"trace_id": trace_id})

    return await with_idempotency(request, await request.body(), run, mode="schema.synthesize")
