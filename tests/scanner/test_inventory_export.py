"""Task 16 (unit): the inventory, the persona/field exports, the CSV guard and the scan evidence
pack — built from stored rows (no browser, no database)."""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import UTC, datetime
from typing import Any

from dps.config import Settings
from dps.engine.service import PrivacyEngine
from dps.engine.verify import normalise
from dps.jobs import Job, JobKind, JobPhase, JobState
from dps.layers.connectors.evidence import LABEL_OBFUSCATE, LABEL_SYNTHESIZE, canonical_json
from dps.layers.scanner.inventory import (
    CREDENTIAL_NOTE,
    build_export,
    build_scan_evidence,
    inventory_csv,
    inventory_rows,
    write_csv,
)
from dps.layers.scanner.models import RiskFlag, ScanFieldOut, ScanPageOut
from dps.layers.scanner.personas_fill import apply_format, persona_values, tokens_for
from dps.layers.scanner.runtime import RunOutcome, ScanRuntime
from tests.scanner.helpers import make_runtime

NOW = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
AADHAAR_FMT = "shape:#### #### ####"


def _page(**kw: Any) -> ScanPageOut:
    base: dict[str, Any] = dict(
        page_no=3,
        url_redacted="http://demo-site:8410/apply/identity",
        depth=1,
        parent_page_no=1,
        status="done",
        discovered_via="link",
        template="demo-site:8410/apply/identity",
        form_count=1,
        field_count=5,
        personal_field_count=4,
        critical_field_count=1,
        artefacts={"filled_synthesize": "ar_1"},
        risk_flags=[
            RiskFlag(
                flag="autocomplete_enabled_sensitive",
                severity="high",
                page_no=3,
                field_nos=[1, 3],
                entity_types=["PERSON_NAME", "AADHAAR"],
            )
        ],
        validation={
            "synthesize": {"checked": 4, "accepted": 4, "rejected": 0},
            "obfuscate": {"checked": 4, "accepted": 3, "rejected": 1},
        },
        capture_status="not_needed",
        created_at=NOW,
        updated_at=NOW,
    )
    return ScanPageOut.model_validate(base | kw)


def _field(field_no: int, **kw: Any) -> ScanFieldOut:
    base: dict[str, Any] = dict(
        page_no=3,
        form_index=1,
        field_no=field_no,
        selector=f"input#f{field_no}",
        tag="input",
        input_type="text",
        name_attr=f"f{field_no}",
        id_attr=f"f{field_no}",
        label=f"Field {field_no}",
        label_source="for",
        autocomplete=None,
        pattern=None,
        maxlength=None,
        required=True,
        options_count=None,
        entity_type=None,
        sensitivity=None,
        confidence=None,
    )
    return ScanFieldOut.model_validate(base | kw)


def _identity(engine: PrivacyEngine, rt: ScanRuntime) -> list[ScanFieldOut]:
    """The rows the crawler stores for the display persona (1): a name, an Aadhaar (tokenised),
    a gender select, a password and a file field."""
    p1 = persona_values(engine, seed=rt.seed, persona_no=1, locale=rt.locale, policy=rt.policy)
    aadhaar = apply_format(AADHAAR_FMT, p1["aadhaar"])
    assert aadhaar is not None
    tok = tokens_for(
        engine,
        {"3": ("AADHAAR", aadhaar)},
        key=rt.key,
        policy=rt.policy,
        locale=rt.locale,
        persona_no=1,
    )
    return [
        _field(
            1,
            label="Full name",
            entity_type="PERSON_NAME",
            sensitivity="HIGH",
            persona_attr="name",
            fill_format="as_is",
            fill_synthesize=p1["name"],
            fill_obfuscate=p1["name"],
            obfuscate_method="keep",
            fill_status="filled",
            risk_codes=["autocomplete_enabled_sensitive"],
        ),
        _field(
            3,
            label="Aadhaar number",
            entity_type="AADHAAR",
            sensitivity="CRITICAL",
            persona_attr="aadhaar",
            fill_format=AADHAAR_FMT,
            fill_synthesize=aadhaar,
            fill_obfuscate=tok.tokens["3"],
            obfuscate_method=tok.methods["3"],
            fill_status="filled",
            risk_codes=["autocomplete_enabled_sensitive"],
        ),
        _field(
            4,
            tag="select",
            input_type="select-one",
            label="Gender",
            entity_type="GENDER",
            sensitivity="MEDIUM",
            persona_attr="gender",
            options_count=4,
            fill_synthesize=p1["gender"],
            fill_obfuscate=p1["gender"],
            obfuscate_method="keep",
            fill_status="filled",
        ),
        _field(
            5,
            input_type="password",
            label="Password",
            entity_type="PASSWORD",
            sensitivity="CRITICAL",
            persona_attr="credential",
            obfuscate_method="credential",
            fill_status="filled",
        ),
        _field(6, input_type="file", label="Proof of address", fill_status="skipped_file"),
    ]


def _job(rt: ScanRuntime, mode: str = "synthesize") -> Job:
    return Job(
        id="s_01TEST",
        tenant_id="t-scan",
        kind=JobKind.scan,
        state=JobState.running,
        phase=JobPhase.scan,
        mode=mode,
        spec={
            "url": "http://demo-site:8410/apply?email=<EMAIL>",
            "url_origin": "http://demo-site:8410",
            "scope": {"same_origin": True, "max_pages": 30, "max_depth": 3, "deny": ["/logout"]},
            "auth": {"storage_state": {"inline": True}},
            "fill": {"personas": 3, "display_persona": 1, "locale": "en_IN"},
            "consistency": {"scope": "key", "key_wrapped": False},
        },
        kid=rt.key.kid,
        created_by="k-test",
        created_at=NOW,
        updated_at=NOW,
        started_at=NOW,
    )


def test_inventory_rows_join_pages_and_fields(
    scan_settings: Settings, scan_engine: PrivacyEngine
) -> None:
    rt = make_runtime("http://demo-site:8410", scan_settings, scan_engine)
    fields = _identity(scan_engine, rt)
    rows = inventory_rows([_page()], list(reversed(fields)))
    assert [r.field_no for r in rows] == [1, 3, 4, 5, 6]
    assert {r.path for r in rows} == {"http://demo-site:8410/apply/identity"}
    assert rows[1].risk_codes == ["autocomplete_enabled_sensitive"]
    assert not any(
        name.startswith("fill_") and name != "fill_status" for name in type(rows[0]).model_fields
    )
    text = inventory_csv(rows).decode("utf-8")
    assert len(text.splitlines()) == 6 and text.splitlines()[0].startswith("page_no,path,")
    for f in fields:  # no fill value in the inventory
        for value in (f.fill_synthesize, f.fill_obfuscate):
            if value:
                assert value not in text


def test_export_rerenders_every_persona_and_matches_the_display_persona(
    scan_settings: Settings, scan_engine: PrivacyEngine
) -> None:
    rt = make_runtime("http://demo-site:8410", scan_settings, scan_engine, mode="obfuscate")
    fields = _identity(scan_engine, rt)
    personas = [
        persona_values(scan_engine, seed=rt.seed, persona_no=n, locale=rt.locale, policy=rt.policy)
        for n in (1, 2, 3)
    ]
    raw_json, raw_csv = build_export(
        job_id="s_01TEST",
        runtime=rt,
        pages=[_page()],
        fields=fields,
        personas=personas,
        engine=scan_engine,
    )
    doc = json.loads(raw_json)
    assert doc["format"] == "dps.scan-export.v1" and doc["personas"] == 3
    assert (
        doc["key"] == {"alg": rt.key.alg, "kid": rt.key.kid} and len(doc["seed_fingerprint"]) == 16
    )
    assert rt.key.dek not in raw_json.decode() and rt.seed.seed not in raw_json.decode()
    by_no = {f["field_no"]: f for f in doc["fields"]}
    aadhaar = by_no[3]["values"]
    assert aadhaar[0]["synthesize"] == fields[1].fill_synthesize
    assert aadhaar[0]["obfuscate"] == fields[1].fill_obfuscate
    assert all(re.fullmatch(r"\d{4} \d{4} \d{4}", v["synthesize"]) for v in aadhaar)
    assert aadhaar[1]["synthesize"] != aadhaar[0]["synthesize"]
    assert aadhaar[1]["obfuscate"] not in (None, aadhaar[1]["synthesize"])
    assert [v["synthesize"] for v in by_no[4]["values"]] == [p["gender"] for p in personas]
    assert [v["synthesize"] for v in by_no[1]["values"]] == [p["name"] for p in personas]
    assert by_no[5]["values"] == [] and by_no[5]["note"] == CREDENTIAL_NOTE
    assert by_no[6]["values"] == [] and by_no[6]["fill_status"] == "skipped_file"
    for entry in doc["persona_set"]:
        assert "credential" not in entry["attributes"] and "otp" not in entry["attributes"]
    for p in personas:  # the credential is never exported
        assert p["credential"] not in raw_json.decode() and p["credential"] not in raw_csv.decode()
    lines = raw_csv.decode("utf-8").splitlines()
    assert len(lines) == 1 + 3 * 3  # header + 3 personas x (name, Aadhaar, gender)
    assert lines[0] == (
        "page_no,path,form_index,field_no,label,entity_type,persona_attr,persona,"
        "synthesize_value,obfuscate_value,obfuscate_method"
    )


def test_csv_guard() -> None:
    data = write_csv(["a"], [['=HYPERLINK("x")'], ["+91 98451 20377"], ["-SUM(1)"], ["@x"], ["-5"]])
    rows = list(csv.reader(io.StringIO(data.decode("utf-8"))))
    assert rows == [
        ["a"],
        ['\'=HYPERLINK("x")'],
        ["+91 98451 20377"],
        ["'-SUM(1)"],
        ["'@x"],
        ["-5"],
    ]
    tabbed = list(csv.reader(io.StringIO(write_csv(["\tx"], [["\rz"], ["+"]]).decode())))
    assert tabbed[0] == ["'\tx"] and tabbed[2] == ["'+"]


def test_evidence_pack_shape_and_labels(
    scan_settings: Settings, scan_engine: PrivacyEngine
) -> None:
    rt = make_runtime("http://demo-site:8410", scan_settings, scan_engine)
    fields = _identity(scan_engine, rt)
    counts = {
        "pages_visited": 1,
        "forms": 1,
        "fields": 5,
        "personal": 4,
        "filled": 1,
        "non_get_allowed": 0,
        "blocked": {"POST": 3, "GET": 1},
        "navigations": 1,
    }
    outcome = RunOutcome(pages_visited=1, fields=5, spa_routes=1, submit_attempts_neutralised=2)
    pack = build_scan_evidence(
        evidence_id="ev_01TEST",
        job=_job(rt),
        runtime=rt,
        pages=[_page()],
        fields=fields,
        counts=counts,
        outcome=outcome,
        result="succeeded",
        errors=[],
        settings=scan_settings,
        exports={"json": "ar_j", "csv": "ar_c"},
    )
    assert pack.job_kind == "scan" and pack.outcome == "succeeded"
    assert pack.label == LABEL_SYNTHESIZE
    assert pack.scan is not None
    assert pack.scan["submissions"] == 0 and pack.scan["non_get_allowed"] == 0
    assert pack.scan["blocked_requests"] == {"POST": 3, "GET": 1}
    assert pack.scan["by_entity"] == {"AADHAAR": 1, "GENDER": 1, "PASSWORD": 1, "PERSON_NAME": 1}
    assert pack.scan["risk_flags"][0]["code"] == "A1" and pack.scan["spa_routes"] == 1
    assert pack.scan["validation"]["obfuscate"] == {"checked": 4, "accepted": 3, "rejected": 1}
    assert pack.reveal_map == [
        {
            "path": "field:3/1/3",
            "entity_type": "AADHAAR",
            "method": fields[1].obfuscate_method,
            "params": {},
            "anchor_path": None,
            "reveal": True,
        }
    ]
    assert pack.source["kind"] == "ui" and pack.source["auth"] == "inline"
    assert pack.source["url"].endswith("email=<EMAIL>")
    assert pack.consistency["kid"] == rt.key.kid and pack.target["exports"]["json"] == "ar_j"
    assert pack.verification["passed"] is True and pack.verification["values_checked"] == 1
    canon = normalise(canonical_json(pack).decode("utf-8"))
    for f in fields:
        for value in (f.fill_synthesize, f.fill_obfuscate):
            if value and len(normalise(value)) >= 4:
                assert normalise(value) not in canon
    assert rt.key.dek not in canonical_json(pack).decode() and rt.seed.seed not in canon
    obf = build_scan_evidence(
        evidence_id="ev_02TEST",
        job=_job(rt, "obfuscate"),
        runtime=None,
        pages=[],
        fields=[],
        counts={"non_get_allowed": 2},
        outcome=None,
        result="failed",
        errors=[{"code": "submission_detected", "where": "guard"}],
        settings=scan_settings,
        exports={},
    )
    assert obf.label == LABEL_OBFUSCATE and obf.verification["passed"] is False
    assert obf.scan is not None and obf.scan["non_get_allowed"] == 2
    assert (
        obf.consistency["seed_fingerprint"] is None
        and obf.errors[0]["code"] == "submission_detected"
    )
