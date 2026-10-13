"""Plain helpers for scanner tests (fixtures live in conftest.py)."""

from __future__ import annotations

from typing import Any

import httpx

from dps.layers.scanner.models import FieldClassification, FieldFacts


def received(url: str) -> dict[str, Any]:
    """The demo site's counters of everything non-GET (and every GET-form submission) it received."""
    data: dict[str, Any] = httpx.get(f"{url}/__received", timeout=5.0).json()
    return data


_FIELD_BASE: dict[str, Any] = dict(
    key="0:1",
    field_no=1,
    form_index=1,
    frame_index=0,
    in_shadow=False,
    tag="input",
    input_type="text",
    name=None,
    id=None,
    label="",
    label_source="none",
    placeholder=None,
    title=None,
    aria_label=None,
    autocomplete=None,
    pattern=None,
    maxlength=None,
    minlength=None,
    required=False,
    readonly=False,
    disabled=False,
    visible=True,
    inputmode=None,
    selector="input",
)


def field_facts(**kw: Any) -> FieldFacts:
    """A visible text input with no attributes, overridden by ``kw``."""
    return FieldFacts.model_validate(_FIELD_BASE | kw)


def classification(entity: str | None, sensitivity: str | None) -> FieldClassification:
    """A classification literal (``confidence=0.9``, detector ``label``) or an unclassified one."""
    if entity is None:
        return FieldClassification(
            entity_type=None, sensitivity=None, confidence=0.0, detectors=[], evidence=[]
        )
    return FieldClassification(
        entity_type=entity,
        sensitivity=sensitivity,
        confidence=0.9,
        detectors=["label"],
        evidence=[],
    )
