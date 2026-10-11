from __future__ import annotations

import copy

import pytest

from dps.engine.jsonpath import flatten, get_path, parse_path, set_path

DOC = {
    "customer_id": "C1",
    "name": "Ann",
    "card": {"pan": "4111", "tags": ["a", {"k.v": 1}]},
    "n": None,
}


def test_flatten_yields_every_scalar_leaf_with_a_path() -> None:
    paths = dict(flatten(DOC))
    assert paths["$.customer_id"] == "C1"
    assert paths["$.card.pan"] == "4111"
    assert paths["$.card.tags[0]"] == "a"
    assert paths["$.card.tags[1]['k.v']"] == 1
    assert paths["$.n"] is None
    assert flatten("plain text") == [("$", "plain text")]


def test_parse_get_set_round_trip() -> None:
    assert parse_path("$.card.tags[1]['k.v']") == ["card", "tags", 1, "k.v"]
    assert get_path(DOC, "$.card.tags[1]['k.v']") == 1
    doc = copy.deepcopy(DOC)
    set_path(doc, "$.card.pan", "tok")
    set_path(doc, "$.card.tags[0]", "b")
    assert doc["card"]["pan"] == "tok" and doc["card"]["tags"][0] == "b"
    assert DOC["card"]["pan"] == "4111"  # original untouched


def test_root_path_on_scalar_document() -> None:
    assert get_path("hello", "$") == "hello"
    with pytest.raises(KeyError):
        get_path(DOC, "$.missing")


def test_every_flattened_path_reads_back_including_odd_keys() -> None:
    odd = {"it's": {"back\\slash": ["x", {"a'\\b": 2, "0": "zero", "número": "n", "": "empty"}]}}
    for path, leaf in flatten(odd):
        assert get_path(odd, path) == leaf
    assert get_path(odd, "$['it\\'s']['back\\\\slash'][1]['0']") == "zero"


def test_malformed_paths_are_refused() -> None:
    for bad in ("card.pan", "$.", "$[x]", "$['unterminated]", "$.a..b"):
        with pytest.raises(ValueError):
            parse_path(bad)
