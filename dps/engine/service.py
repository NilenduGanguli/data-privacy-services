"""``PrivacyEngine`` — the façade the Layer 1 routers (and later the job workers) call.

Request flow: resolve policy → detect → plan → transform → verify → report. Obfuscation is CPU
work and synchronous; synthesis is async because the optional LLM rewrite is.

Every error a caller can cause is an :class:`EngineError` whose message names the rule, never a
value, a key or a seed; the request models hide their input in validation errors for the same
reason (they carry customer data and key material).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dps.config import Settings
from dps.engine.detect.custom import custom_detector_from
from dps.engine.detect.ensemble import Detector, DetectorConfig
from dps.engine.detect.llm_label import (
    LLM_LABEL_TIER,
    LabelVerdict,
    gateway_usable,
    label,
    leftover_fields,
    template_of,
)
from dps.engine.detect.ner_presidio import build_ner
from dps.engine.findings import Finding, Locator, Plan, hash_value
from dps.engine.jsonpath import Scalar, flatten
from dps.engine.planner import build_plan, explain_plan
from dps.engine.policy import Policy, resolve_policy
from dps.engine.report import Report, build_report
from dps.engine.synth.llm_text import synthesize_text
from dps.engine.synth.surrogates import (
    SurrogateEngine,
    SynthesisSeed,
    generate_synthesis_seed,
    seed_bytes,
    seed_from_caller,
)
from dps.engine.taxonomy import Sensitivity, entity_info, record_sensitivity
from dps.engine.transform import fpe
from dps.engine.transform.apply import (
    ApplyOptions,
    OutputSegment,
    RevealHint,
    anchor_for,
    apply_plan,
    reveal,
)
from dps.engine.transform.crypto import (
    KeyMaterial,
    generate_key_material,
    key_material_from_caller,
    wrap_key_material,
)
from dps.engine.transform.methods import CipherFactory, MethodError
from dps.engine.verify import (
    NerState,
    Verification,
    assess_coverage,
    collect_originals,
    verify_output,
)
from dps.llm.gateway import LlmGateway
from dps.observability import FINDINGS, TRANSFORMS, VERIFY_FAIL, get_logger

logger = get_logger(__name__)

WRAP_TYPES = ("rsa-oaep-256",)
_HIDDEN = ConfigDict(hide_input_in_errors=True)


class EngineError(ValueError):
    """A request the engine cannot honour (bad policy, bad key material, bad hints)."""


class VerificationFailed(EngineError):
    """The leakage sweep found an original value in the output under a strict policy."""


class Payload(BaseModel):
    """Exactly one of a JSON document (``data``) or a text."""

    model_config = _HIDDEN

    data: Any | None = None
    text: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Payload:
        if (self.data is None) == (self.text is None):
            raise ValueError("provide exactly one of data or text")
        return self


class ConsistencyOptions(BaseModel):
    """Per-request consistency: scope, and the caller-held key material or synthesis seed."""

    model_config = _HIDDEN

    scope: Literal["request", "key", "entity"] | None = None
    key_material: dict[str, Any] | None = None
    synthesis_seed: dict[str, Any] | None = None


class RequestOptions(BaseModel):
    """Per-request overrides of the policy (locale, token style, key wrapping, LLM rewrite)."""

    model_config = _HIDDEN

    locale: str | None = None
    token_style: Literal["envelope", "natural"] | None = None
    wrap_key: dict[str, str] | None = None
    consistency: ConsistencyOptions = Field(default_factory=ConsistencyOptions)
    llm_rewrite: bool | None = None
    # Documents ask for it (M1); M0 callers never see segments unless they do.
    return_segments: bool = False


class DetectResponse(BaseModel):
    """Findings (no values), the record's sensitivity and the report."""

    findings: list[Finding]
    record_sensitivity: Sensitivity
    report: Report


class PlanResponse(BaseModel):
    """Findings and the reviewer-facing plan rows."""

    findings: list[Finding]
    plan: list[dict[str, Any]]
    record_sensitivity: Sensitivity


class TransformResponse(BaseModel):
    """The transformed payload plus what the caller needs to reveal or stay consistent."""

    result: Any
    findings: list[Finding]
    plan: list[dict[str, Any]]
    key_material: KeyMaterial | None = None
    reveal_hints: list[RevealHint] = Field(default_factory=list)
    synthesis_seed: SynthesisSeed | None = None
    # Where each replacement landed (``options.return_segments``); ``None`` unless asked.
    segments: list[OutputSegment] | None = None
    report: Report


class RevealResponse(BaseModel):
    """The revealed payload."""

    result: Any
    revealed: int
    report: Report


#: T3 never outranks a checksum: a label's score is capped here (spec §5.3).
T3_SCORE_CAP = 0.85


class Leftovers(list[Finding]):
    """T3 findings for one document, plus the labelling calls made for them (0 or 1).

    A plain list of findings to every caller; the engine operations read ``llm_calls`` from it
    for ``report.llm.calls``.
    """

    def __init__(self, findings: Sequence[Finding] = (), *, llm_calls: int = 0) -> None:
        super().__init__(findings)
        self.llm_calls = llm_calls


class LeftoverLabels(BaseModel):
    """T3 verdicts by path template, relative to a document (value-free), for a stream: one
    labelling call, then :meth:`PrivacyEngine.apply_labels` per item without the LLM."""

    verdicts: dict[str, LabelVerdict] = Field(default_factory=dict)
    llm_calls: int = 0


#: A document's leaves T0–T2 left without a finding, grouped by path template.
LeafGroups = dict[str, list[tuple[str, Scalar]]]


def _kept(plan: Plan) -> set[tuple[str, int | None, int | None]]:
    return {
        (e.finding.locator.path, e.finding.locator.start, e.finding.locator.end)
        for e in plan.entries
        if e.method == "keep"
    }


def _label_calls(extra_findings: Sequence[Finding]) -> int:
    """The T3 labelling calls behind ``extra_findings`` (a :class:`Leftovers` knows; else 0)."""
    return int(getattr(extra_findings, "llm_calls", 0))


class PrivacyEngine:
    """Stateless engine: one instance per process, safe to share across requests."""

    def __init__(
        self,
        settings: Settings,
        *,
        gateway: LlmGateway | None = None,
        cipher_factory: CipherFactory | None = None,
    ) -> None:
        self.settings = settings
        self.gateway = gateway
        self.cipher_factory = cipher_factory
        self.ner = build_ner(tuple(settings.ner_languages_list), settings.ner_enabled)
        self.fpe_enabled = settings.fpe_enabled and (
            cipher_factory is not None or fpe.fpe_available()
        )

    # -- helpers ------------------------------------------------------------------------------
    def policy(self, ref: str | dict[str, Any] | None) -> Policy:
        """Resolve a preset name, an inline policy or the configured default."""
        try:
            return resolve_policy(ref, default_name=self.settings.default_policy)
        except (KeyError, ValueError) as exc:
            raise EngineError(f"invalid policy: {exc}") from None

    def _locale(self, policy: Policy, options: RequestOptions) -> str:
        """Request locale, else the policy's pin (``locale.default``), else ``DPS_DEFAULT_LOCALE``."""
        return options.locale or policy.locale.default or self.settings.default_locale

    def detector(self, policy: Policy, locale: str) -> Detector:
        """The detector this engine would use for ``policy`` (L2 runs it over free-text columns)."""
        return self._detector(policy, locale)

    def surrogate_engine(self, seed: SynthesisSeed, locale: str) -> SurrogateEngine:
        """A fresh, seeded surrogate engine (bounded persona cache: create one per batch)."""
        return SurrogateEngine(seed_bytes(seed), default_locale=locale)

    def _detector(self, policy: Policy, locale: str) -> Detector:
        config = DetectorConfig(
            min_score=policy.detect.min_score,
            locale=locale,
            ner_enabled="ner" in policy.detect.tiers and self.ner is not None,
        )
        try:
            custom = custom_detector_from(
                policy.detect.custom_recognizers, policy.detect.declared_fields
            )
        except ValueError as exc:
            raise EngineError(f"invalid custom recognizers: {exc}") from None
        return Detector(config, ner=self.ner, custom=custom)

    def _detect(
        self, payload: Payload, detector: Detector, extra: Sequence[Finding] = ()
    ) -> tuple[list[Finding], Sensitivity]:
        """T0–T2 (and T4) findings, merged with ``extra`` (T3) on leaves they left untouched."""
        result = detector.detect(data=payload.data, text=payload.text)
        findings, sensitivity = list(result.findings), result.record_sensitivity
        if extra and payload.data is not None:
            covered = {f.locator.path for f in findings}
            findings += [f for f in extra if f.locator.path not in covered]
            sensitivity = record_sensitivity(f.entity_type for f in findings)
        for f in findings:
            for d in f.detectors:
                FINDINGS.labels(f.entity_type, d.split(":")[0]).inc()
        return findings, sensitivity

    # -- T3: LLM metadata labelling (opt-in, value-free; spec §5.3) ---------------------------
    def _t3_enabled(self, policy: Policy) -> bool:
        return LLM_LABEL_TIER in policy.detect.tiers and gateway_usable(self.gateway)

    def labelling_enabled(self, policy_ref: str | dict[str, Any] | None) -> bool:
        """True when T3 would run for this policy: it lists ``llm_label`` and a real (non-stub)
        LLM provider is configured."""
        return gateway_usable(self.gateway) and self._t3_enabled(self.policy(policy_ref))

    @staticmethod
    def _leftover_groups(data: Any, detector: Detector) -> LeafGroups:
        """String and number leaves with no T0–T2 finding, grouped by template (``[n]`` → ``[*]``)."""
        covered = {f.locator.path for f in detector.detect(data=data).findings}
        groups: LeafGroups = {}
        for path, value in flatten(data):
            if path in covered or value is None or isinstance(value, bool):
                continue
            if isinstance(value, str) and not value.strip():
                continue
            groups.setdefault(template_of(path), []).append((path, value))
        return groups

    @staticmethod
    def _t3_findings(
        groups: LeafGroups, verdicts: dict[str, LabelVerdict], policy: Policy
    ) -> list[Finding]:
        out: list[Finding] = []
        for template, verdict in verdicts.items():
            score = round(min(verdict.confidence, T3_SCORE_CAP), 3)
            if score < policy.detect.min_score:
                continue
            info = entity_info(verdict.entity_type)
            out += [
                Finding(
                    locator=Locator(path=path),
                    entity_type=verdict.entity_type,
                    score=score,
                    detectors=[LLM_LABEL_TIER],
                    sensitivity=info.sensitivity,
                    evidence=[LLM_LABEL_TIER],
                    value_hash=hash_value(value),
                )
                for path, value in groups.get(template, [])
            ]
        return out

    async def _label_groups(self, groups: LeafGroups) -> tuple[dict[str, LabelVerdict], int]:
        values = {template: [value for _, value in leaves] for template, leaves in groups.items()}
        outcome = await label(
            leftover_fields(values),
            gateway=self.gateway,
            settings=self.settings,
            protected=[str(v) for vs in values.values() for v in vs],
        )
        return {v.ref: v for v in outcome.verdicts}, outcome.llm_calls

    async def label_leftovers(
        self,
        payload: Payload,
        policy_ref: str | dict[str, Any] | None,
        options: RequestOptions,
    ) -> Leftovers:
        """T3 findings for the JSON leaves T0–T2 left without one (empty unless the policy's
        ``detect.tiers`` holds ``llm_label`` and a real LLM provider is configured).

        One labelling call per document: each leaf template becomes a value-free
        :class:`~dps.engine.detect.llm_label.FieldMeta`; every leaf of a labelled template gets a
        ``Finding`` (detector ``llm_label``, score ≤ 0.85). Pass the result to the operation as
        ``extra_findings``.
        """
        if payload.data is None or not gateway_usable(self.gateway):
            return Leftovers()  # before resolving the policy: the common, LLM-free case
        policy = self.policy(policy_ref)
        if not self._t3_enabled(policy):
            return Leftovers()
        detector = self._detector(policy, self._locale(policy, options))
        groups = await asyncio.to_thread(self._leftover_groups, payload.data, detector)
        if not groups:
            return Leftovers()
        verdicts, calls = await self._label_groups(groups)
        return Leftovers(self._t3_findings(groups, verdicts, policy), llm_calls=calls)

    async def label_templates(
        self,
        documents: Sequence[Any],
        policy_ref: str | dict[str, Any] | None,
        options: RequestOptions,
    ) -> LeftoverLabels:
        """One labelling call for the leftover templates of several JSON documents (an NDJSON
        stream's first chunk); :meth:`apply_labels` then applies the verdicts per document."""
        if not documents or not gateway_usable(self.gateway):
            return LeftoverLabels()
        policy = self.policy(policy_ref)
        if not self._t3_enabled(policy):
            return LeftoverLabels()
        detector = self._detector(policy, self._locale(policy, options))

        def collect() -> LeafGroups:
            merged: LeafGroups = {}
            for document in documents:
                for template, leaves in self._leftover_groups(document, detector).items():
                    merged.setdefault(template, []).extend(leaves)
            return merged

        groups = await asyncio.to_thread(collect)
        if not groups:
            return LeftoverLabels()
        verdicts, calls = await self._label_groups(groups)
        return LeftoverLabels(verdicts=verdicts, llm_calls=calls)

    def apply_labels(
        self,
        payload: Payload,
        policy_ref: str | dict[str, Any] | None,
        options: RequestOptions,
        labels: LeftoverLabels,
    ) -> Leftovers:
        """T3 findings for a document from verdicts obtained earlier (no LLM call)."""
        if not labels.verdicts or payload.data is None:
            return Leftovers()
        policy = self.policy(policy_ref)
        detector = self._detector(policy, self._locale(policy, options))
        groups = self._leftover_groups(payload.data, detector)
        return Leftovers(self._t3_findings(groups, labels.verdicts, policy))

    def _plan(self, findings: list[Finding], policy: Policy, mode: str) -> Plan:
        try:
            return build_plan(findings, policy, mode, fpe_enabled=self.fpe_enabled)
        except ValueError as exc:
            raise EngineError(str(exc)) from None

    def _apply_options(
        self,
        policy: Policy,
        key: KeyMaterial | None,
        surrogates: SurrogateEngine | None,
        options: RequestOptions,
        locale: str,
    ) -> ApplyOptions:
        return ApplyOptions(
            policy=policy,
            key=key,
            surrogates=surrogates,
            fpe_enabled=self.fpe_enabled,
            cipher_factory=self.cipher_factory,
            token_style=options.token_style or policy.output.token_style,
            locale=locale,
        )

    @staticmethod
    def _ner_state(detector: Detector) -> NerState:
        """``on`` when NER ran for this request, ``unavailable`` when its model failed to load."""
        if not detector.config.ner_enabled or detector.ner is None:
            return "off"
        return "on" if getattr(detector.ner, "available", True) else "unavailable"

    def _verify(
        self,
        findings: list[Finding],
        payload: Payload,
        output: Any,
        plan: Plan,
        policy: Policy,
        mode: str,
        detector: Detector,
    ) -> Verification:
        originals = collect_originals(findings, data=payload.data, text=payload.text)
        ner = self._ner_state(detector)
        detectors = [
            *(["structural"] if payload.data is not None else []),
            "patterns",
            "name_cues",
            *(["ner"] if ner == "on" else []),
            *(["custom"] if detector.custom is not None else []),
        ]
        kept = _kept(plan)
        coverage = assess_coverage(
            data=payload.data,
            text=payload.text,
            findings=findings,
            kept=kept,
            detectors=detectors,
            ner=ner,
        )
        source = payload.text if payload.text is not None else payload.data
        verification = verify_output(originals, output, kept=kept, coverage=coverage, source=source)
        if not verification.passed:
            VERIFY_FAIL.labels(mode).inc()
            logger.warning("verification_failed", mode=mode, hits=len(verification.hits))
            if policy.verify.leakage_check == "strict":
                raise VerificationFailed("output failed the leakage sweep; nothing was returned")
        return verification

    # -- operations ---------------------------------------------------------------------------
    def detect(
        self,
        payload: Payload,
        policy_ref: str | dict[str, Any] | None,
        options: RequestOptions,
        *,
        extra_findings: Sequence[Finding] = (),
    ) -> DetectResponse:
        """Findings (locators, types, scores; never values) and the record's sensitivity.

        ``extra_findings`` (T3, :meth:`label_leftovers`) join the detectors' on JSON leaves the
        detectors left untouched; so for every operation below.
        """
        t0 = time.perf_counter()
        policy = self.policy(policy_ref)
        findings, sensitivity = self._detect(
            payload, self._detector(policy, self._locale(policy, options)), extra_findings
        )
        report = build_report(
            mode="detect",
            policy_name=policy.name,
            policy_hash=policy.config_hash(),
            findings=findings,
            plan=None,
            methods_used={},
            notes={},
            reversible_values=0,
            verification=None,
            llm_calls=_label_calls(extra_findings),
            duration_ms=(time.perf_counter() - t0) * 1000,
        )
        report.record_sensitivity = str(sensitivity)
        return DetectResponse(findings=findings, record_sensitivity=sensitivity, report=report)

    def plan(
        self,
        payload: Payload,
        policy_ref: str | dict[str, Any] | None,
        options: RequestOptions,
        *,
        mode: str,
        extra_findings: Sequence[Finding] = (),
    ) -> PlanResponse:
        """What ``obfuscate`` / ``synthesize`` would do, with reasons, without doing it."""
        policy = self.policy(policy_ref)
        findings, sensitivity = self._detect(
            payload, self._detector(policy, self._locale(policy, options)), extra_findings
        )
        plan = self._plan(findings, policy, mode)
        return PlanResponse(
            findings=findings, plan=explain_plan(plan), record_sensitivity=sensitivity
        )

    def obfuscate(
        self,
        payload: Payload,
        policy_ref: str | dict[str, Any] | None,
        options: RequestOptions,
        *,
        extra_findings: Sequence[Finding] = (),
    ) -> TransformResponse:
        """Reversible (and irreversible) obfuscation under a per-request or caller-held key.

        Raises:
            VerificationFailed: an original survived and the policy's leakage check is strict.
            EngineError: bad policy, key material, wrap key or method parameters.
        """
        t0 = time.perf_counter()
        policy = self.policy(policy_ref)
        locale = self._locale(policy, options)
        detector = self._detector(policy, locale)
        findings, _ = self._detect(payload, detector, extra_findings)
        plan = self._plan(findings, policy, "obfuscate")
        scope = options.consistency.scope or policy.consistency.scope
        try:
            key = (
                key_material_from_caller(options.consistency.key_material)
                if options.consistency.key_material
                else generate_key_material(scope)
            )
        except ValueError as exc:
            raise EngineError(f"invalid key material: {exc}") from None
        wrap_pem = self._wrap_pem(options)
        try:
            applied = apply_plan(
                plan=plan,
                options=self._apply_options(policy, key, None, options, locale),
                data=payload.data,
                text=payload.text,
            )
        except MethodError as exc:
            raise EngineError(str(exc)) from None
        for method, count in applied.methods_used.items():
            TRANSFORMS.labels("obfuscate", method).inc(count)
        verification = self._verify(
            findings, payload, applied.output, plan, policy, "obfuscate", detector
        )
        key_out = key
        if wrap_pem is not None:
            try:
                key_out = wrap_key_material(key, wrap_pem)
            except ValueError as exc:
                raise EngineError(f"wrap_key: {exc}") from None
        report = build_report(
            mode="obfuscate",
            policy_name=policy.name,
            policy_hash=policy.config_hash(),
            findings=findings,
            plan=plan,
            methods_used=applied.methods_used,
            notes=applied.notes,
            reversible_values=len(applied.reveal_hints),
            verification=verification,
            llm_calls=_label_calls(extra_findings),
            duration_ms=(time.perf_counter() - t0) * 1000,
        )
        return TransformResponse(
            result=applied.output,
            findings=findings,
            plan=explain_plan(plan),
            key_material=key_out,
            reveal_hints=applied.reveal_hints,
            segments=applied.segments if options.return_segments else None,
            report=report,
        )

    @staticmethod
    def _wrap_pem(options: RequestOptions) -> str | None:
        """The caller's public key PEM when ``wrap_key`` is set (checked before any work)."""
        if not options.wrap_key:
            return None
        wrap_type = options.wrap_key.get("type", WRAP_TYPES[0])
        if wrap_type not in WRAP_TYPES:
            raise EngineError(f"wrap_key: type must be one of {', '.join(WRAP_TYPES)}")
        pem = options.wrap_key.get("public_key_pem", "")
        if not pem:
            raise EngineError("wrap_key: public_key_pem is required")
        return pem

    async def synthesize(
        self,
        payload: Payload,
        policy_ref: str | dict[str, Any] | None,
        options: RequestOptions,
        *,
        extra_findings: Sequence[Finding] = (),
    ) -> TransformResponse:
        """Synthetic surrogates (and, for text, an optional placeholder-only LLM rewrite).

        The synthesis seed is returned unless the consistency scope is ``request``.

        Raises:
            VerificationFailed: an original survived and the policy's leakage check is strict.
            EngineError: bad policy or seed, or a method that cannot run without a key.
        """
        t0 = time.perf_counter()
        policy = self.policy(policy_ref)
        locale = self._locale(policy, options)
        detector = self._detector(policy, locale)
        findings, _ = self._detect(payload, detector, extra_findings)
        plan = self._plan(findings, policy, "synthesize")
        scope = options.consistency.scope or policy.consistency.scope
        try:
            seed = (
                seed_from_caller(options.consistency.synthesis_seed)
                if options.consistency.synthesis_seed
                else generate_synthesis_seed()
            )
            surrogates = SurrogateEngine(seed_bytes(seed), default_locale=locale)
        except ValueError as exc:
            raise EngineError(f"invalid synthesis seed: {exc}") from None
        llm_rewrite = (
            options.llm_rewrite
            if options.llm_rewrite is not None
            else policy.synthesize.llm_rewrite_text
        )
        llm_rewrite = llm_rewrite and self.settings.llm_text_rewrite
        llm: dict[str, Any] = {"used": False, "fallback": None}
        llm_calls = _label_calls(extra_findings)
        output: Any
        methods_used: dict[str, int]
        notes: dict[str, int]
        segments: list[OutputSegment]
        if payload.text is not None:
            try:
                output, info = await synthesize_text(
                    payload.text,
                    findings,
                    surrogates=surrogates,
                    locale=locale,
                    anchor=anchor_for(payload.text, plan),
                    gateway=self.gateway,
                    llm_rewrite=llm_rewrite,
                    plan=plan,
                    screen=lambda text: len(detector.detect(text=text).findings),
                )
            except MethodError as exc:
                raise EngineError(str(exc)) from None
            segments = info.pop("segments", [])  # replacement text: never logged with ``info``
            llm_calls += int(info["llm_calls"])
            llm = {"used": bool(info["llm_used"]), "fallback": info["fallback"]}
            methods_used = dict(info["methods_used"])
            notes = dict(info["notes"])
            if info["fallback"]:
                notes[f"llm:{info['fallback']}"] = 1
        else:
            try:
                applied = apply_plan(
                    plan=plan,
                    options=self._apply_options(policy, None, surrogates, options, locale),
                    data=payload.data,
                )
            except MethodError as exc:
                raise EngineError(str(exc)) from None
            output, methods_used, notes = applied.output, applied.methods_used, applied.notes
            segments = applied.segments
        for method, count in methods_used.items():
            TRANSFORMS.labels("synthesize", method).inc(count)
        verification = self._verify(findings, payload, output, plan, policy, "synthesize", detector)
        report = build_report(
            mode="synthesize",
            policy_name=policy.name,
            policy_hash=policy.config_hash(),
            findings=findings,
            plan=plan,
            methods_used=methods_used,
            notes=notes,
            reversible_values=0,
            verification=verification,
            llm_calls=llm_calls,
            duration_ms=(time.perf_counter() - t0) * 1000,
        )
        report.llm.update(llm)
        return TransformResponse(
            result=output,
            findings=findings,
            plan=explain_plan(plan),
            synthesis_seed=seed if scope != "request" else None,
            segments=segments if options.return_segments else None,
            report=report,
        )

    def reveal(
        self,
        payload: Payload,
        key_material: dict[str, Any],
        hints: list[dict[str, Any]],
    ) -> RevealResponse:
        """Undo an obfuscation with the caller's key material and (optionally) reveal hints.

        Raises:
            EngineError: invalid key material or hints, a key that does not match the hints, or a
                token that does not decrypt. The message names the rule or the error type only.
        """
        t0 = time.perf_counter()
        try:
            key = key_material_from_caller(key_material)
            parsed = [RevealHint.model_validate(h) for h in hints]
        except ValueError as exc:
            raise EngineError(f"invalid reveal request: {exc}") from None
        policy = self.policy(None)
        options = ApplyOptions(
            policy=policy,
            key=key,
            surrogates=None,
            fpe_enabled=self.fpe_enabled,
            cipher_factory=self.cipher_factory,
            locale=self.settings.default_locale,
        )
        output: Any = payload.data if payload.data is not None else payload.text
        try:
            result = reveal(output=output, key=key, hints=parsed, options=options)
        except MethodError as exc:  # value-free by contract: names the rule
            raise EngineError(f"reveal failed: {exc}") from None
        except (KeyError, IndexError, ValueError) as exc:
            raise EngineError(f"reveal failed: {type(exc).__name__}") from None
        report = build_report(
            mode="reveal",
            policy_name=policy.name,
            policy_hash=policy.config_hash(),
            findings=[],
            plan=None,
            methods_used={},
            notes={},
            reversible_values=len(parsed),
            verification=None,
            llm_calls=0,
            duration_ms=(time.perf_counter() - t0) * 1000,
        )
        return RevealResponse(result=result, revealed=len(parsed), report=report)
