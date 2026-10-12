from __future__ import annotations

import importlib.util
import re
import traceback
from pathlib import Path

import pytest
from pydantic import ValidationError

from dps.engine.detect.checksums import validate
from dps.evaluation import corpus
from dps.evaluation.corpus import JURISDICTIONS, generate_corpus, load_corpus, write_corpus


def test_generation_is_deterministic(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    write_corpus(generate_corpus(7, 6), a)
    write_corpus(generate_corpus(7, 6), b)
    assert [p.read_bytes() for p in sorted(a.iterdir())] == [
        p.read_bytes() for p in sorted(b.iterdir())
    ]
    assert load_corpus([a])[1] == load_corpus([b])[1]
    write_corpus(generate_corpus(8, 6), tmp_path / "c")
    assert load_corpus([tmp_path / "c"])[1] != load_corpus([a])[1]


def test_labels_slice_rule_valid_values() -> None:
    corpus = generate_corpus(7, 12)
    assert set(corpus) == set(JURISDICTIONS)
    for records in corpus.values():
        for record in records:
            for label in record.labels:
                if record.kind == "text":
                    value = (record.text or "")[label.start or 0 : label.end or 0]
                elif label.start is None:
                    value = str((record.data or {})[label.path[2:]])
                else:
                    value = str((record.data or {})["notes"])[label.start : label.end or 0]
                assert (
                    value and "{" not in value and validate(label.entity_type, value) is not False
                )


def test_values_vary_across_records() -> None:
    cards = {
        r.data["card_number"] for r in generate_corpus(7, 40)["US"] if r.kind == "json" and r.data
    }
    assert len(cards) == 20


def test_templates_hold_no_digits_or_addresses() -> None:
    from dps.evaluation.corpus import NOTE_TEMPLATES, TEXT_TEMPLATES

    for template in [t for ts in TEXT_TEMPLATES.values() for t in ts] + list(NOTE_TEMPLATES):
        assert (
            not re.search(r"\d", re.sub(r"\{[A-Z_]+(#\d)?\}", "", template)) and "@" not in template
        )


# --- A bank's labelled corpus is customer data: a bad line must never echo it (review G5 B1) ---

_GOOD = (
    '{"id":"ok-1","jurisdiction":"US","locale":"en_US","kind":"text",'
    '"text":"SSN 123-45-6789","labels":[{"start":4,"end":15,"entity_type":"SSN"}]}'
)
_HEAD = '{"id":"bad-1","jurisdiction":"US","locale":"en_US",'
_MALFORMED = {
    "truncated": (_HEAD + '"kind":"text","text":"SSN SENTINEL-123-45-6789","labels":[}').encode(),
    "wrong_type": (
        _HEAD + '"kind":"text","text":"x","labels":[{"entity_type":"SSN","start":"SENTINEL-st"}]}'
    ).encode(),
    "bad_literal": (_HEAD + '"kind":"SENTINEL-kind","text":"x"}').encode(),
    "not_an_object": b'"SENTINEL-bare-string"',
    "bad_list": (_HEAD + '"kind":"json","data":{"a":1},"labels":"SENTINEL-labels"}').encode(),
    "not_utf8": (_HEAD + '"kind":"text","text":"SENTINEL-utf8 ').encode() + b'\xff\xfe"}',
}


@pytest.mark.parametrize("case", sorted(_MALFORMED))
def test_malformed_line_error_names_file_and_line_only(tmp_path: Path, case: str) -> None:
    path = tmp_path / "bank-private.jsonl"
    path.write_bytes(_GOOD.encode() + b"\n\n" + _MALFORMED[case] + b"\n")
    with pytest.raises(ValueError) as info:
        load_corpus([path])
    err = info.value
    assert isinstance(err, corpus.CorpusError)
    # no chained pydantic / codec error that still holds the line (or the whole file)
    assert err.__cause__ is None and err.__context__ is None
    rendered = "".join(traceback.format_exception(err)) + repr(err) + repr(err.args)
    assert "SENTINEL" not in rendered and "123-45-6789" not in rendered
    assert "bank-private.jsonl line 3" in str(err)  # physical line number, blank line counted


def test_record_models_hide_input_in_errors() -> None:
    with pytest.raises(ValidationError) as info:
        corpus.CorpusRecord.model_validate(
            {"id": "x", "jurisdiction": "US", "locale": "en_US", "kind": "SENTINEL-kind"}
        )
    assert "SENTINEL" not in str(info.value)
    with pytest.raises(ValidationError) as info:
        corpus.Label.model_validate({"entity_type": "SSN", "start": "SENTINEL-start"})
    assert "SENTINEL" not in str(info.value)


def test_unicode_line_separators_inside_values_do_not_split_a_record(tmp_path: Path) -> None:
    record = corpus.CorpusRecord(
        id="u-1", jurisdiction="EU", locale="en_GB", kind="text", text="a b c\x85d"
    )
    write_corpus({"EU": [record]}, tmp_path)
    records, _ = load_corpus([tmp_path])
    assert [r.text for r in records] == ["a b c\x85d"]


def test_cli_reports_a_malformed_corpus_without_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = importlib.util.spec_from_file_location(
        "eval_detectors_cli", Path(__file__).resolve().parents[1] / "tools" / "eval_detectors.py"
    )
    assert spec is not None and spec.loader is not None
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    path = tmp_path / "bank-private.jsonl"
    path.write_bytes(_MALFORMED["truncated"] + b"\n")
    assert cli.main(["run", "--corpus", str(path)]) == 2
    out, err = capsys.readouterr()
    assert "SENTINEL" not in out + err and "123-45-6789" not in out + err
    assert "bank-private.jsonl line 1" in err and "Traceback" not in err
