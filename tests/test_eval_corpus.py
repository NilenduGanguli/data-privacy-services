from __future__ import annotations

import re
from pathlib import Path

from dps.engine.detect.checksums import validate
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
