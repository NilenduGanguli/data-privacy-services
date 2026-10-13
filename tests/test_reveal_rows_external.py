"""L2 reveal of ``external_tokenize`` columns (M5 G2 review, open item of Task 8; SEC-4).

``tools/reveal_rows.py`` runs on the key holder's machine. For an ``external_tokenize`` column it
detokenizes through the bank tokenization service with the **key holder's own** credentials
(``--tokenizer-url``, ``--tokenizer-token-file``) — DPS's credentials are never involved, so DPS is
no deputy — and only after the presented key matched the evidence pack's key id. One detokenize
call per entity type and profile per chunk of rows; every answer must tokenize back to the stored
token. The counting mock proves what reached the service.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from dps.engine.batch import BatchTransformer, ColumnPlan, external_reveal_cache
from dps.engine.policy import MethodSpec, load_preset
from dps.engine.service import PrivacyEngine
from dps.engine.transform.crypto import generate_key_material
from dps.engine.transform.methods import MethodError
from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape
from tests.test_external_binding import TOKEN, _Mock, _settings
from tools import reveal_rows

SHAPE = TableShape(
    object=ObjectRef(owner="bank", name="people"),
    key=["id"],
    key_source="primary_key",
    columns=[
        ColumnInfo(path="$.id", name="id", data_type="bigint", kind="int", is_key=True),
        ColumnInfo(path="$.ssn", name="ssn", data_type="text", kind="text"),
        ColumnInfo(path="$.segment", name="segment", data_type="text", kind="text"),
    ],
)
PLAN = ColumnPlan(
    path="$.ssn",
    name="ssn",
    entity_type="SSN",
    method=MethodSpec(method="external_tokenize", params={"profile": "ssn"}),
    text_column=False,
    reversible=True,
    kind="text",
    length=None,
)
REVEAL_MAP = [
    {
        "path": "$.ssn",
        "entity_type": "SSN",
        "method": "external_tokenize",
        "params": {"profile": "ssn"},
        "anchor_path": None,
        "reveal": True,
        "text_column": False,
    }
]
ROWS = [{"id": i, "ssn": f"123-45-{6700 + i}", "segment": "retail"} for i in range(25)]


@pytest.fixture
def mock() -> Iterator[_Mock]:
    m = _Mock()
    yield m
    m.server.shutdown()


def _masked(mock: _Mock) -> tuple[list[dict[str, Any]], Any]:
    engine = PrivacyEngine(_settings(), tokenizer=mock.tokenizer())
    key = generate_key_material("key")
    t = BatchTransformer(
        engine,
        policy=load_preset("gdpr-pseudonymise"),
        mode="obfuscate",
        columns=[PLAN],
        shape=SHAPE,
        key=key,
        seed=None,
        locale="en_US",
    )
    rows = t.transform([dict(r) for r in ROWS]).rows
    assert all(r["ssn"].startswith("tk_") for r in rows)
    return rows, key


def _files(tmp_path: Path, rows: list[dict[str, Any]], key: Any, kid: str) -> list[str]:
    docs = {
        "evidence": {"pack": {"reveal_map": REVEAL_MAP, "consistency": {"kid": kid}}},
        "snapshot": {"shape": SHAPE.model_dump(mode="json"), "columns": []},
        "key": key.model_dump(mode="json"),
    }
    for name, doc in docs.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(doc), encoding="utf-8")
    (tmp_path / "rows.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    (tmp_path / "token").write_text(TOKEN + "\n", encoding="utf-8")
    return [
        "--evidence",
        str(tmp_path / "evidence.json"),
        "--snapshot",
        str(tmp_path / "snapshot.json"),
        "--key",
        str(tmp_path / "key.json"),
        "--rows",
        str(tmp_path / "rows.jsonl"),
        "--policy",
        "gdpr-pseudonymise",
    ]


def test_the_tool_reveals_external_columns_with_the_key_holders_credentials(
    tmp_path: Path, mock: _Mock, capsys: pytest.CaptureFixture[str]
) -> None:
    rows, key = _masked(mock)
    argv = _files(tmp_path, rows, key, key.kid)
    argv += ["--tokenizer-url", mock.url, "--tokenizer-token-file", str(tmp_path / "token")]
    argv += ["--chunk-rows", "10"]
    assert reveal_rows.main(argv) == 0
    captured = capsys.readouterr()
    assert [json.loads(line) for line in captured.out.splitlines()] == ROWS
    assert "revealed 25 rows" in captured.err
    # one detokenize call per chunk (10 + 10 + 5 rows), each checked by tokenizing back
    assert mock.received["/v1/detokenize"] == 3


def test_without_a_tokenizer_the_column_is_refused_by_name(
    tmp_path: Path, mock: _Mock, capsys: pytest.CaptureFixture[str]
) -> None:
    rows, key = _masked(mock)
    assert reveal_rows.main(_files(tmp_path, rows, key, key.kid)) == 1
    refused = capsys.readouterr()
    assert refused.out == "" and "$.ssn" in refused.err and "--tokenizer-url" in refused.err
    assert "123-45" not in refused.err and "tk_" not in refused.err


def test_another_key_never_reaches_the_service(
    tmp_path: Path, mock: _Mock, capsys: pytest.CaptureFixture[str]
) -> None:
    rows, key = _masked(mock)
    other = generate_key_material("key")
    argv = _files(tmp_path, rows, other, key.kid)  # the pack names the job's key, not this one
    argv += ["--tokenizer-url", mock.url, "--tokenizer-token-file", str(tmp_path / "token")]
    assert reveal_rows.main(argv) == 1
    refused = capsys.readouterr()
    assert refused.out == "" and "key id" in refused.err
    assert mock.received["/v1/detokenize"] == 0


def test_wrong_credentials_and_tampered_tokens_fail_without_values(
    tmp_path: Path, mock: _Mock, capsys: pytest.CaptureFixture[str]
) -> None:
    rows, key = _masked(mock)
    argv = _files(tmp_path, rows, key, key.kid)
    (tmp_path / "bad").write_text("not-the-token", encoding="utf-8")
    bad = [*argv, "--tokenizer-url", mock.url, "--tokenizer-token-file", str(tmp_path / "bad")]
    assert reveal_rows.main(bad) == 1
    assert "HTTP 401" in capsys.readouterr().err
    tampered = [dict(r) for r in rows]
    tampered[3]["ssn"] = "tk_" + "a" * 40
    _files(tmp_path, tampered, key, key.kid)
    good = [*argv, "--tokenizer-url", mock.url, "--tokenizer-token-file", str(tmp_path / "token")]
    assert reveal_rows.main(good) == 1
    refused = capsys.readouterr()
    assert refused.out == "" and "123-45" not in refused.err and "tk_" not in refused.err


def test_the_tokenizer_url_must_be_https_unless_it_is_this_machine(tmp_path: Path) -> None:
    (tmp_path / "token").write_text(TOKEN, encoding="utf-8")
    base = ["--evidence", "e", "--snapshot", "s", "--key", "k", "--rows", "r"]
    args = reveal_rows.parse_args([*base, "--tokenizer-url", "http://tokenizer.bank.example:8420"])
    with pytest.raises(ValueError, match="https"):
        reveal_rows.build_cli_tokenizer(args)
    for url in ("https://tokenizer.bank.example", "http://127.0.0.1:8420", "http://localhost:1"):
        args = reveal_rows.parse_args(
            [*base, "--tokenizer-url", url, "--tokenizer-token-file", str(tmp_path / "token")]
        )
        assert reveal_rows.build_cli_tokenizer(args) is not None
    assert reveal_rows.build_cli_tokenizer(reveal_rows.parse_args(base)) is None
    assert isinstance(reveal_rows.parse_args(base), argparse.Namespace)


class _Liar:
    """A service that answers detokenize with a value that does not tokenize back."""

    name = "liar"

    def tokenize(self, entity_type: str, values: Any, *, profile: str | None) -> list[str]:
        return ["tk_other" for _ in values]

    def detokenize(self, entity_type: str, tokens: Any, *, profile: str | None) -> list[str]:
        return ["999-99-9999" for _ in tokens]

    def health(self) -> bool:
        return True


def test_an_answer_that_does_not_tokenize_back_is_refused() -> None:
    rows = [{"id": 1, "ssn": "tk_abc", "segment": "retail"}]
    with pytest.raises(MethodError) as err:
        external_reveal_cache(rows, reveal_map=REVEAL_MAP, shape=SHAPE, tokenizer=_Liar())
    assert "999-99" not in str(err.value) and "tk_abc" not in str(err.value)
    assert external_reveal_cache(rows, reveal_map=[], shape=SHAPE, tokenizer=_Liar()) == {}
