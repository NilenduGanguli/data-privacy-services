"""WP-I (m3 merge): D-A in free text. A synthesize run never copies a LOW personal attribute into a
synthetic table — the CITY *column* is surrogated (M5 G2 B1) — yet the free-text pipeline kept the
same entity's spans by policy (``keep_non_sensitive``), so ``NOTES`` carried the source row's real
city. On Oracle's CRM demo (``NOTES``: "… Prefers branch in <city>") the leakage sweep caught it
and failed every synthesize run closed. A kept span is surrogated in a synthesize job now."""

from __future__ import annotations

from typing import Any

from dps.config import Settings
from dps.engine.batch import BatchTransformer, ColumnPlan
from dps.engine.policy import MethodSpec, load_preset
from dps.engine.service import PrivacyEngine
from dps.engine.synth.surrogates import generate_synthesis_seed
from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape

ENGINE = PrivacyEngine(
    Settings(_env_file=None, llm_provider="stub", ner_enabled=False, fpe_enabled=False)  # type: ignore[call-arg]
)
SHAPE = TableShape(
    object=ObjectRef(owner="CRM", name="CUSTOMERS"),
    key=["ID"],
    key_source="primary_key",
    columns=[
        ColumnInfo(path="$.ID", name="ID", data_type="NUMBER(12)", kind="int", is_key=True),
        ColumnInfo(path="$.CITY", name="CITY", data_type="VARCHAR2(80)", kind="text"),
        ColumnInfo(path="$.NOTES", name="NOTES", data_type="CLOB", kind="lob_text"),
    ],
)
CITIES = ["Pune", "Mumbai", "Chennai", "Kolkata", "Hyderabad"]


def _plan(name: str, entity: str, *, text: bool) -> ColumnPlan:
    return ColumnPlan(
        path=f"$.{name}",
        name=name,
        entity_type=entity,
        method=MethodSpec(method="surrogate"),
        text_column=text,
        reversible=False,
        kind="lob_text" if text else "text",
        length=None,
    )


def test_a_city_kept_in_free_text_is_surrogated_in_a_synthesize_job() -> None:
    rows: list[dict[str, Any]] = [
        {"ID": i, "CITY": CITIES[i % 5], "NOTES": f"Prefers branch in {CITIES[i % 5]}."}
        for i in range(50)
    ]
    transformer = BatchTransformer(
        ENGINE,
        policy=load_preset("gdpr-pseudonymise"),
        mode="synthesize",
        columns=[_plan("CITY", "CITY", text=False), _plan("NOTES", "FREE_TEXT", text=True)],
        shape=SHAPE,
        key=None,
        seed=generate_synthesis_seed(),
        locale="en_IN",
        unclassified="keep",
        verify="warn",
    )
    result = transformer.transform(rows)
    assert result.verification.passed, [
        (h["path"], h["entity_type"], h["rule"]) for h in result.verification.hits
    ]
    copied = sum(src["CITY"] in out["NOTES"] for src, out in zip(rows, result.rows, strict=True))
    assert copied == 0
