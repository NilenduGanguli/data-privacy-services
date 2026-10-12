"""The policy model (spec §5.4): what to detect, how sure to be, what to do, how to stay consistent.

Presets live as YAML next to this module. ``resolve_policy`` accepts a preset name, ``None`` (the
configured default) or a dict that may name a ``preset`` and override any section.
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import cache
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from dps.engine.detect.custom import DeclaredField, RecognizerSpec

PRESET_DIR = Path(__file__).parent / "presets"
_PRESET_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")

#: Inline policies arrive in request bodies and may carry deny lists or dictionary values, so a
#: validation error names the field and the rule, never the input (same rule as the settings).
_MODEL_CONFIG = ConfigDict(extra="forbid", hide_input_in_errors=True)

#: Every method the transform registry (Task 15) implements. Kept here so policies validate
#: without importing the registry; ``methods.py`` asserts it implements exactly this set.
METHOD_NAMES: tuple[str, ...] = (
    "det_token",
    "fpe_digits",
    "fpe_text",
    "email",
    "phone",
    "date_shift",
    "hmac_pseudonym",
    "mask_partial",
    "redact",
    "null",
    "bucket",
    "generalize",
    "dictionary",
    "noise",
    "keep",
    "surrogate",
    "external_tokenize",
)


class MethodSpec(BaseModel):
    """``{"method": "fpe_digits", "keep_suffix": 4}`` → ``method="fpe_digits", params={...}``."""

    model_config = _MODEL_CONFIG
    method: str
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _flatten(cls, data: Any) -> Any:
        if isinstance(data, dict) and "params" not in data:
            data = dict(data)
            method = data.pop("method", None)
            return {"method": method, "params": data}
        return data

    @field_validator("method")
    @classmethod
    def _known(cls, value: str) -> str:
        if value not in METHOD_NAMES:
            raise ValueError(f"unknown method {value!r}; known: {', '.join(METHOD_NAMES)}")
        return value


class LocaleConfig(BaseModel):
    """Locale for surrogates and phone parsing; optionally per record.

    ``default`` is a pin: ``None`` (most presets) lets the service's ``DPS_DEFAULT_LOCALE`` apply;
    a value (``dpdp-pseudonymise`` pins ``en_IN``) wins over it. A request's ``options.locale``
    wins over both.
    """

    model_config = _MODEL_CONFIG
    default: str | None = None
    by_country_field: str | None = None


class DetectConfig(BaseModel):
    """Which tiers run, the score threshold, and the client's own declarations."""

    model_config = _MODEL_CONFIG
    tiers: list[str] = Field(default_factory=lambda: ["structural", "patterns", "ner"])
    min_score: float = Field(default=0.6, ge=0.0, le=1.0)
    custom_recognizers: list[RecognizerSpec] = Field(default_factory=list)
    declared_fields: list[DeclaredField] = Field(default_factory=list)


JsonScalar = str | int | float | bool | None
#: A tabular column path: ``$.`` and one SQL column name (tabular synthesis is per table).
COLUMN_PATH = r"^\$\.[A-Za-z_][A-Za-z0-9_$#]{0,127}$"
ColumnPath = Annotated[str, Field(pattern=COLUMN_PATH)]


class WhenCondition(BaseModel):
    """``{"path": "$.BALANCE", "op": "<", "value": 0}`` — compared against the synthetic row."""

    model_config = _MODEL_CONFIG
    path: ColumnPath
    op: Literal["<", "<=", ">", ">=", "==", "!=", "in", "not_in", "is_null", "not_null"]
    value: JsonScalar | list[JsonScalar] = None


class WhenRule(BaseModel):
    """``{"if": {...}, "then": "OVERDRAWN"}``; the first matching rule of a constraint wins."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, populate_by_name=True)
    if_: WhenCondition = Field(alias="if")
    then: JsonScalar = None

    @model_serializer(mode="wrap")
    def _dump_if(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        # always dumped as "if" (the input form), whether or not the caller asks for aliases
        data = dict(handler(self))
        if "if_" in data:
            data["if"] = data.pop("if_")
        return data


class TabularConstraint(BaseModel):
    """A rule the tabular engine enforces on every synthetic row (spec §5.6).

    ``object`` limits it to one table (``OWNER.NAME``, case-insensitive); ``None`` applies it
    wherever the column exists. ``unique`` stands alone; ``min``/``max``/``positive`` and
    ``when`` may be combined. Literals here are configuration, never customer data.
    """

    model_config = _MODEL_CONFIG
    path: ColumnPath
    object: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_$#.]{1,257}$")
    min: float | str | None = None
    max: float | str | None = None
    positive: bool = False
    unique: bool = False
    when: list[WhenRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def _coherent(self) -> TabularConstraint:
        ranged = self.min is not None or self.max is not None or self.positive
        if self.unique and (ranged or self.when):
            raise ValueError(
                f"constraint on {self.path}: unique cannot be combined with other rules"
            )
        if not (self.unique or ranged or self.when):
            raise ValueError(f"constraint on {self.path} sets no rule")
        return self


class SynthesizeConfig(BaseModel):
    """Methods for synthesize mode: a default plus per-entity overrides; tabular constraints and
    the quasi-identifiers the privacy metric counts (M5)."""

    model_config = _MODEL_CONFIG
    default: MethodSpec = Field(default_factory=lambda: MethodSpec(method="surrogate"))
    overrides: dict[str, MethodSpec] = Field(default_factory=dict)
    keep_non_sensitive: bool = True
    llm_rewrite_text: bool = False
    constraints: list[TabularConstraint] = Field(default_factory=list)
    quasi_identifiers: list[ColumnPath] = Field(default_factory=list)  # "$.NAME" for the QI metric


class ConsistencyConfig(BaseModel):
    """How far the same input maps to the same output (request, key, entity)."""

    model_config = _MODEL_CONFIG
    scope: Literal["request", "key", "entity"] = "key"
    field_scoped: bool = False


class OutputConfig(BaseModel):
    """How replaced values look: natural (format-preserving) or envelope tokens."""

    model_config = _MODEL_CONFIG
    token_style: Literal["envelope", "natural"] = "natural"


class VerifyConfig(BaseModel):
    """What the leakage sweep does on a hit: fail the request or only warn."""

    model_config = _MODEL_CONFIG
    leakage_check: Literal["strict", "warn"] = "strict"


class Policy(BaseModel):
    """One effective policy: detection, treatment, synthesis, consistency, output, verify."""

    model_config = _MODEL_CONFIG
    name: str = "custom"
    version: int = 1
    description: str = ""
    locale: LocaleConfig = Field(default_factory=LocaleConfig)
    detect: DetectConfig = Field(default_factory=DetectConfig)
    treat: dict[str, MethodSpec] = Field(default_factory=dict)
    synthesize: SynthesizeConfig = Field(default_factory=SynthesizeConfig)
    consistency: ConsistencyConfig = Field(default_factory=ConsistencyConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    verify: VerifyConfig = Field(default_factory=VerifyConfig)

    def method_for(self, entity_type: str, mode: str) -> MethodSpec:
        """The method for ``entity_type`` in ``mode`` (``obfuscate`` | ``synthesize``)."""
        if mode == "synthesize":
            return self.synthesize.overrides.get(entity_type, self.synthesize.default)
        return (
            self.treat.get(entity_type)
            or self.treat.get("default_sensitive")
            or MethodSpec(method="det_token")
        )

    def config_hash(self) -> str:
        """Stable sha256 of the policy for reports and evidence packs.

        The M5 synthesize lists (``constraints``, ``quasi_identifiers``) count only when set, so
        every hash recorded before M5 (presets, snapshots, evidence packs) still matches.
        """
        data = self.model_dump(mode="json")
        for name in _OPTIONAL_SYNTHESIZE:
            if not data["synthesize"].get(name):
                data["synthesize"].pop(name, None)
        canon = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()


#: ``synthesize`` fields (M5) left out of :meth:`Policy.config_hash` while empty.
_OPTIONAL_SYNTHESIZE = ("constraints", "quasi_identifiers")

#: Sections whose values are whole method specs: an override replaces an entry, never merges
#: into it (``{"method": "fpe_digits"}`` must not inherit the preset's ``keep_suffix``).
_METHOD_MAPS: frozenset[tuple[str, ...]] = frozenset({("treat",), ("synthesize", "overrides")})
_METHOD_VALUES: frozenset[tuple[str, ...]] = frozenset({("synthesize", "default")})


def _deep_merge(
    base: dict[str, Any], override: dict[str, Any], path: tuple[str, ...] = ()
) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        here = (*path, key)
        if path in _METHOD_MAPS or here in _METHOD_VALUES:
            out[key] = value
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value, here)
        else:
            out[key] = value
    return out


@cache
def preset_names() -> frozenset[str]:
    """The names of the bundled preset files."""
    return frozenset(p.stem for p in PRESET_DIR.glob("*.yaml") if _PRESET_NAME.fullmatch(p.stem))


@cache
def _load_preset_cached(name: str) -> Policy:
    data = yaml.safe_load((PRESET_DIR / f"{name}.yaml").read_text(encoding="utf-8")) or {}
    return Policy.model_validate(data)


def load_preset(name: str) -> Policy:
    """Load and validate ``presets/<name>.yaml``; ``KeyError`` when there is no such preset.

    Only the bundled preset names are accepted (a name is never joined into a filesystem path
    unchecked), and every call returns a private deep copy, so a caller that edits its policy
    never changes what the next request gets.
    """
    if name not in preset_names():
        shown = f" {name!r}" if _PRESET_NAME.fullmatch(name) else ""
        raise KeyError(f"unknown policy preset{shown}")
    return _load_preset_cached(name).model_copy(deep=True)


def list_presets() -> list[dict[str, str]]:
    """``[{"name", "description"}]`` for every preset file."""
    out = []
    for name in sorted(preset_names()):
        policy = _load_preset_cached(name)
        out.append({"name": policy.name, "description": policy.description})
    return out


#: Presets meant for the synthesize mode (all others are obfuscation presets).
PRESET_MODES: dict[str, str] = {
    "synthetic-realistic": "synthesize",
    "synthetic-strict": "synthesize",
}
#: The methods a caller's key can undo — ``Method.reversible`` in ``transform/methods.py``, which
#: imports this module, so the set is spelled out here and a test keeps the two equal.
REVERSIBLE_METHODS = frozenset(
    {"det_token", "fpe_digits", "fpe_text", "email", "phone", "date_shift", "external_tokenize"}
)


def preset_summaries() -> list[dict[str, Any]]:
    """``list_presets()`` plus version, config hash, intended mode and reversibility (console)."""
    out: list[dict[str, Any]] = []
    for item in list_presets():
        policy = load_preset(item["name"])
        mode = PRESET_MODES.get(policy.name, "obfuscate")
        reversible = mode == "obfuscate" and any(
            spec.method in REVERSIBLE_METHODS for spec in policy.treat.values()
        )
        out.append(
            {
                **item,
                "version": policy.version,
                "hash": policy.config_hash(),
                "mode": mode,
                "reversible": reversible,
            }
        )
    return out


def resolve_policy(ref: str | dict[str, Any] | None, *, default_name: str) -> Policy:
    """Preset name, inline dict (optionally ``{"preset": name, ...overrides}``) or the default."""
    if ref is None:
        return load_preset(default_name)
    if isinstance(ref, str):
        return load_preset(ref)
    override = dict(ref)
    preset = override.pop("preset", None)
    if preset is None:
        return Policy.model_validate(override)
    base = load_preset(str(preset)).model_dump(mode="json")
    merged = _deep_merge(base, override)
    if "name" not in override:
        merged["name"] = f"{preset}+overrides"  # reports must not pass overrides off as the preset
    return Policy.model_validate(merged)
