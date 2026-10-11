from __future__ import annotations

import copy
import itertools

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
    # "\u0663" is ARABIC-INDIC DIGIT THREE: ``\d`` would accept it and int() would read 3.
    for bad in ("card.pan", "$.", "$[x]", "$['unterminated]", "$.a..b", "$[\u0663]"):
        with pytest.raises(ValueError):
            parse_path(bad)


def test_keys_that_merely_look_like_identifiers_are_quoted() -> None:
    # A trailing newline must not slip through the bare ``.ident`` form (``re.match`` with ``$``
    # accepts "name\n"); such paths could not be parsed back.
    for key in ("a\n", "\n", "name\n", "a]", "$", "0", "a b", "a.b", "[0]", "_ok"):
        doc = {key: {"inner": [key]}}
        for path, leaf in flatten(doc):
            assert parse_path(path)[0] == key, repr(key)
            assert get_path(doc, path) == leaf, repr(key)
    assert flatten({"a\n": 1}) == [("$['a\n']", 1)]
    assert flatten({"_ok": 1}) == [("$._ok", 1)]


def test_brute_force_round_trip_over_awkward_alphabet() -> None:
    alphabet = ["a", "_", "0", "\\", "'", ".", "\n", "]", "[", "$", " ", "\u00e9"]
    for length in range(4):
        for chars in itertools.product(alphabet, repeat=length):
            key = "".join(chars)
            doc = {key: [1, {key: 2}]}
            pairs = flatten(doc)
            assert [leaf for _, leaf in pairs] == [1, 2], repr(key)
            for path, leaf in pairs:
                assert get_path(doc, path) == leaf, repr(key)
                copy_doc = copy.deepcopy(doc)
                set_path(copy_doc, path, "x")
                assert get_path(copy_doc, path) == "x", repr(key)
