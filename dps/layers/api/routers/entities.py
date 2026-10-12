"""GET /api/v1/entities and POST /api/v1/policies/test-recognizer (console Policies page).

The recognizer test answers with offsets only: the sample text is neither stored nor echoed, and
nothing is registered (a recognizer's ``sensitivity`` is dropped before the detector is built, so a
test cannot change the process-wide taxonomy). Python's ``re`` has no timeout, so the route bounds
the text (20 000 characters), each pattern (500 characters) and the number of patterns (50).
"""

from __future__ import annotations

from typing import Any

import yaml
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from dps.auth import require_scope
from dps.engine.detect.custom import PolicyCustomDetector, RecognizerSpec
from dps.engine.taxonomy import TAXONOMY, Family, entity_info, is_builtin_entity
from dps.layers.api.common import CodedHTTPException
from dps.layers.documents.models import validation_message
from dps.ratelimit import enforce_rate_limit

router = APIRouter(prefix="/api/v1", tags=["policies"])
MAX_PATTERN_CHARS = 500
MAX_PATTERNS = 50


class EntityOut(BaseModel):
    name: str
    family: str
    sensitivity: str
    description: str
    jurisdictions: list[str]
    builtin: bool


class EntitiesOut(BaseModel):
    items: list[EntityOut]
    families: list[str]


class RecognizerTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    recognizer: dict[str, Any]
    text: str = Field(max_length=20_000)


class RecognizerMatch(BaseModel):
    start: int
    end: int
    entity_type: str
    score: float
    evidence: str
    context: bool


class RecognizerTestOut(BaseModel):
    matches: list[RecognizerMatch]
    count: int
    yaml: str


@router.get(
    "/entities",
    response_model=EntitiesOut,
    dependencies=[Depends(require_scope("detect")), Depends(enforce_rate_limit)],
)
def entities() -> EntitiesOut:
    """The live taxonomy (built-in types and registered custom ones), by family then name."""
    # entity_info: a built-in entry always wins, even if the open registry was mutated
    items = sorted((entity_info(name) for name in list(TAXONOMY)), key=lambda e: (e.family, e.name))
    return EntitiesOut(
        items=[
            EntityOut(
                name=e.name,
                family=e.family.value,
                sensitivity=e.sensitivity.value,
                description=e.description,
                jurisdictions=list(e.jurisdictions),
                builtin=is_builtin_entity(e.name),
            )
            for e in items
        ],
        families=[f.value for f in Family],
    )


@router.post(
    "/policies/test-recognizer",
    response_model=RecognizerTestOut,
    dependencies=[Depends(require_scope("detect")), Depends(enforce_rate_limit)],
)
def test_recognizer(body: RecognizerTestRequest) -> RecognizerTestOut:
    """Run one recognizer over a caller's sample; offsets, scores and the recognizer as YAML."""
    try:
        spec = RecognizerSpec.model_validate(body.recognizer)
    except ValidationError as exc:
        raise CodedHTTPException(422, "invalid_recognizer", validation_message(exc)) from None
    if any(len(p.regex) > MAX_PATTERN_CHARS for p in spec.patterns):
        raise CodedHTTPException(
            422, "invalid_recognizer", f"patterns are limited to {MAX_PATTERN_CHARS} characters"
        )
    if len(spec.patterns) > MAX_PATTERNS:
        raise CodedHTTPException(
            422, "invalid_recognizer", f"a recognizer is limited to {MAX_PATTERNS} patterns"
        )
    detector = PolicyCustomDetector([spec.model_copy(update={"sensitivity": None})], [])
    base = {p.name: p.score for p in spec.patterns}
    matches = [
        RecognizerMatch(
            start=h.start,
            end=h.end,
            entity_type=h.entity_type,
            score=h.score,
            evidence=h.evidence,
            context=h.evidence.startswith("custom-pattern:")
            and h.score > base.get(h.evidence.removeprefix("custom-pattern:"), h.score),
        )
        for h in sorted(detector.scan(body.text, None), key=lambda h: (h.start, h.end))
    ]
    document = {"recognizers": [spec.model_dump(mode="json", exclude_defaults=True)]}
    return RecognizerTestOut(
        matches=matches,
        count=len(matches),
        yaml=yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
    )
