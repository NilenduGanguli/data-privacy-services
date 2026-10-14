"""WP-I (m3 merge): M5's D-A rule (synthetic tables never carry linkable real identifiers) on the
M3 engines.

* A MongoDB collection's key is ``_id``, usually an ObjectId (creation time, machine and counter of
  the *real* document). D-A pseudonymises every written unclassified key, but no pseudonym fitted an
  ObjectId: every MongoDB synthesize into a table or files was refused
  (``synthesize_pseudonym_unsupported``). An ObjectId now gets a keyed ObjectId pseudonym.
* A key-less Oracle table is paged on the ``ROWID`` pseudo key, which is read-only and never
  written: it is not an identifier to pseudonymise (nothing would be written for it).
"""

from __future__ import annotations

from typing import Any

import pytest

from dps.config import Settings
from dps.engine.batch import BatchTransformer, PseudonymColumn
from dps.engine.policy import load_preset
from dps.engine.service import PrivacyEngine
from dps.engine.synth.keys import KeyPseudonyms, PseudonymError
from dps.engine.synth.surrogates import generate_synthesis_seed
from dps.jobs_models import ClassificationSnapshot
from dps.layers.connectors.base import ColumnInfo
from dps.layers.connectors.targets import pseudonym_columns
from tests.connectors.factories import _classified, accepted_snapshot

bson = pytest.importorskip("bson")  # the [connectors] extra (pymongo ships bson)

SEED = b"\x07" * 32


def _mongo_snapshot() -> ClassificationSnapshot:
    snap = accepted_snapshot("c_src", "profiles")
    oid = ColumnInfo(path="$._id", name="_id", data_type="objectId", kind="object_id", is_key=True)
    others = [c.model_copy(update={"is_key": False}) for c in snap.shape.columns]
    shape = snap.shape.model_copy(update={"key": ["_id"], "columns": [oid, *others]})
    columns = [
        _classified(
            oid, None, *(2 * [snap.columns[0].method.model_copy(update={"method": "keep"})])
        ),
        *[c.model_copy(update={"is_key": False}) for c in snap.columns],
    ]
    return snap.model_copy(
        update={"engine": "mongo", "object_ref": "profiles", "shape": shape, "columns": columns}
    )


def _keyless_oracle_snapshot() -> ClassificationSnapshot:
    snap = accepted_snapshot("c_src", "CRM.ADDRESS_HISTORY")
    rowid = ColumnInfo(
        path="$.ROWID", name="ROWID", data_type="ROWID", kind="int", is_key=True, pseudo=True
    )
    keep = snap.columns[0].method.model_copy(update={"method": "keep"})
    others = [c.model_copy(update={"is_key": False}) for c in snap.shape.columns]
    shape = snap.shape.model_copy(
        update={"key": ["ROWID"], "key_source": "rowid", "columns": [*others, rowid]}
    )
    columns = [
        *[
            c.model_copy(update={"is_key": False, "entity_type": None, "sensitivity": None})
            if c.name == "customer_id"
            else c
            for c in snap.columns
        ],
        _classified(rowid, None, keep, keep),
    ]
    return snap.model_copy(
        update={
            "engine": "oracle",
            "object_ref": "CRM.ADDRESS_HISTORY",
            "shape": shape,
            "columns": columns,
        }
    )


def test_an_object_id_pseudonym_is_a_keyed_object_id_never_the_source() -> None:
    keys = KeyPseudonyms(SEED)
    source = [f"65f0c0ffee{i:014x}" for i in range(200)]
    out = [keys.text("KEY|crm.profiles._id", s, kind="object_id") for s in source]
    assert all(len(o) == 24 and int(o, 16) >= 0 for o in out)
    assert len(set(out)) == len(out) and not set(out) & set(source)
    assert out == [keys.text("KEY|crm.profiles._id", s, kind="object_id") for s in source]
    assert out != [
        KeyPseudonyms(b"\x08" * 32).text("KEY|crm.profiles._id", s, kind="object_id")
        for s in source
    ]  # noqa: E501
    with pytest.raises(PseudonymError):
        keys.text("KEY|x", "not-an-object-id", kind="object_id")


@pytest.mark.parametrize("write_mode", ["table", "files"])
def test_a_mongo_synthesize_pseudonymises_its_object_id_key(write_mode: str) -> None:
    snap = _mongo_snapshot()
    projection = list(dict.fromkeys(c.name for c in snap.shape.columns))
    got = pseudonym_columns(
        snap,
        mode="synthesize",
        write_mode=write_mode,
        pk_strategy=None,
        projection=projection,  # type: ignore[arg-type]
    )
    by_name = {p.name: p for p in got}
    assert by_name["_id"].kind == "object_id"
    assert str(by_name["_id"].label).startswith("KEY|") and str(by_name["_id"].label).endswith(
        "profiles._id"
    )


def test_the_batch_writes_an_object_id_pseudonym_in_the_source_type() -> None:
    snap = _mongo_snapshot()
    engine = PrivacyEngine(
        Settings(_env_file=None, llm_provider="stub", ner_enabled=False, fpe_enabled=False)  # type: ignore[call-arg]
    )
    pseudonym = PseudonymColumn(
        path="$._id", name="_id", entity_type="KEY", label="KEY|profiles._id", kind="object_id"
    )
    transformer = BatchTransformer(
        engine,
        policy=load_preset("gdpr-pseudonymise"),
        mode="synthesize",
        columns=[],
        shape=snap.shape,
        key=None,
        seed=generate_synthesis_seed(),
        locale="en_IN",
        unclassified="keep",
        pseudonyms=[pseudonym],
    )
    rows: list[dict[str, Any]] = [{"_id": bson.ObjectId(), "country_code": "IN"} for _ in range(50)]
    result = transformer.transform(rows)
    written = [r["_id"] for r in result.rows]
    assert all(isinstance(w, bson.ObjectId) for w in written)
    assert not {str(w) for w in written} & {str(r["_id"]) for r in rows}
    assert len(set(written)) == 50 and result.verification.passed


def test_a_rowid_pseudo_key_is_never_pseudonymised() -> None:
    snap = _keyless_oracle_snapshot()
    projection = list(dict.fromkeys(c.name for c in snap.shape.columns))
    for write_mode in ("table", "files"):
        got = pseudonym_columns(
            snap,
            mode="synthesize",
            write_mode=write_mode,
            pk_strategy=None,
            projection=projection,  # type: ignore[arg-type]
        )
        assert "ROWID" not in {p.name for p in got}, write_mode
