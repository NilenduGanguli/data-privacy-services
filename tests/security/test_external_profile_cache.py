"""S3 review F-19: the token caches are keyed by profile too. Two columns of one entity type with
different profiles (per-column snapshot overrides: a PCI-restricted and an ops data element) were
both written with the last profile's tokens, and the L2 reveal cache mixed them the same way."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from dps.config import Settings
from dps.engine.batch import BatchTransformer, ColumnPlan, external_reveal_cache
from dps.engine.policy import MethodSpec, resolve_policy
from dps.engine.service import PrivacyEngine
from dps.engine.transform.crypto import generate_key_material
from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape

KEK = "ERERERERERERERERERERERERERERERERERERERERERE="


class _ProfileTokenizer:
    """Deterministic per (profile, value), reversible: ``<profile>~<reversed value>``."""

    name = "profiles"

    def tokenize(
        self, entity_type: str, values: Sequence[str], *, profile: str | None
    ) -> list[str]:
        return [f"{profile}~{v[::-1]}" for v in values]

    def detokenize(
        self, entity_type: str, tokens: Sequence[str], *, profile: str | None
    ) -> list[str]:
        return [t.split("~", 1)[1][::-1] if t.startswith(f"{profile}~") else "?" for t in tokens]

    def health(self) -> bool:
        return True

    def close(self) -> None:
        return None


SHAPE = TableShape(
    object=ObjectRef(owner="bank", name="cards"),
    key=["ID"],
    key_source="primary_key",
    columns=[
        ColumnInfo(path="$.ID", name="ID", data_type="int", kind="int", is_key=True),
        ColumnInfo(path="$.PAN_PCI", name="PAN_PCI", data_type="text", kind="text"),
        ColumnInfo(path="$.PAN_OPS", name="PAN_OPS", data_type="text", kind="text"),
    ],
)


def _plan(name: str, profile: str) -> ColumnPlan:
    return ColumnPlan(
        path=f"$.{name}",
        name=name,
        entity_type="CARD_PAN",
        method=MethodSpec(method="external_tokenize", params={"profile": profile}),
        text_column=False,
        reversible=True,
        kind="text",
        length=None,
    )


def test_each_column_is_tokenized_under_its_own_profile_and_revealed_from_it() -> None:
    settings = Settings(
        _env_file=None, llm_provider="stub", ner_enabled=False, fpe_enabled=False, job_kek=KEK
    )
    engine = PrivacyEngine(settings, tokenizer=_ProfileTokenizer())  # type: ignore[arg-type]
    t = BatchTransformer(
        engine,
        policy=resolve_policy("gdpr-pseudonymise", default_name="gdpr-pseudonymise"),
        mode="obfuscate",
        columns=[_plan("PAN_PCI", "pan-restricted"), _plan("PAN_OPS", "pan-ops")],
        shape=SHAPE,
        key=generate_key_material("key"),
        seed=None,
        locale="en_US",
    )
    pans = [f"41111111111{1000 + i}" for i in range(20)]
    rows: list[dict[str, Any]] = [
        {"ID": i, "PAN_PCI": pan, "PAN_OPS": pan} for i, pan in enumerate(pans)
    ]
    out = t.transform(rows).rows
    assert all(r["PAN_PCI"].startswith("pan-restricted~") for r in out)
    assert all(r["PAN_OPS"].startswith("pan-ops~") for r in out)
    reveal_map = [
        {
            "path": "$.PAN_PCI",
            "entity_type": "CARD_PAN",
            "method": "external_tokenize",
            "reveal": True,
            "params": {"profile": "pan-restricted"},
        },
        {
            "path": "$.PAN_OPS",
            "entity_type": "CARD_PAN",
            "method": "external_tokenize",
            "reveal": True,
            "params": {"profile": "pan-ops"},
        },
    ]
    cache = external_reveal_cache(
        out, reveal_map=reveal_map, shape=SHAPE, tokenizer=_ProfileTokenizer()
    )  # type: ignore[arg-type]
    for row, pan in zip(out, pans, strict=True):
        assert cache[("CARD_PAN", "pan-restricted", row["PAN_PCI"])] == pan
        assert cache[("CARD_PAN", "pan-ops", row["PAN_OPS"])] == pan
