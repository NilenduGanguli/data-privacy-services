"""Reveal L2 output rows with the caller's key — run **by the key holder, locally** (contract §6.2).

    python tools/reveal_rows.py --evidence pack.json --snapshot snapshot.json --key key.json \\
        --rows rows.jsonl [--locale en_IN] [--policy <preset or policy.json>] \\
        [--tokenizer-url https://… --tokenizer-token-file token.txt [--tokenizer-ca-file ca.pem]] \\
        > revealed.jsonl

- ``--evidence``: the evidence pack (``GET /api/v1/evidence/{id}``'s answer or its ``pack``); its
  ``reveal_map`` says which columns reveal and with which method parameters;
- ``--snapshot``: the accepted snapshot the run used (``GET /api/v1/snapshots/{id}``): the table
  shape and the anchor column;
- ``--key``: the key material ``POST /jobs/run`` returned once (or a job group's key);
- ``--rows``: JSON lines as exported (``files`` jsonl) or selected by the operator from a target;
- ``--tokenizer-url`` / ``--tokenizer-token-file`` / ``--tokenizer-ca-file`` (M5): only for a run
  that used ``external_tokenize``. They name the bank tokenization service and **the key holder's
  own** credentials for it: DPS's credentials never detokenize an L2 value (SEC-4), and the bank's
  service decides whether this person may. https is required unless the URL is this machine.

Each output row is revealed with :func:`dps.engine.batch.reveal_row` (a candidate is accepted only
when the forward method maps it back to the stored token, so a wrong key fails instead of
returning wrong values). ``--locale`` must be the run's locale (the policy's, else the worker's
``DPS_DEFAULT_LOCALE``; ``en_IN`` in the compose stack). DPS never stores the key; this tool makes no network call except, for ``external_tokenize``
columns, to the tokenization service named above — after the key matched the evidence pack's key
id, one detokenize call per entity type and profile per ``--chunk-rows`` rows, each answer checked
by tokenizing it back (:func:`dps.engine.batch.external_reveal_cache`). Errors name a line number
and a path, never a value.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator, Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # `python tools/reveal_rows.py`

from dps.config import Settings  # noqa: E402
from dps.engine.batch import external_reveal_cache, reveal_row  # noqa: E402
from dps.engine.service import EngineError, PrivacyEngine  # noqa: E402
from dps.engine.transform.crypto import key_material_from_caller  # noqa: E402
from dps.engine.transform.external import (  # noqa: E402
    ExternalServiceError,
    ExternalTokenizer,
    HttpTokenizer,
)
from dps.engine.transform.methods import MethodError  # noqa: E402
from dps.layers.connectors.base import TableShape  # noqa: E402

#: Hosts a plain-http tokenizer URL may name (the key holder's own machine).
_LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class TokenizerUrlRefused(ValueError):
    """The tokenizer URL is plain http to another machine (value-free)."""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """The CLI (module docstring)."""
    p = argparse.ArgumentParser(prog="python tools/reveal_rows.py")
    p.add_argument("--evidence", type=Path, required=True)
    p.add_argument("--snapshot", type=Path, required=True)
    p.add_argument("--key", type=Path, required=True)
    p.add_argument("--rows", type=Path, required=True)
    p.add_argument("--locale", default="en_US")
    p.add_argument("--policy", default=None, help="a preset name or a policy JSON file")
    p.add_argument("--tokenizer-url", default=None, help="external_tokenize columns only")
    p.add_argument("--tokenizer-token-file", type=Path, default=None)
    p.add_argument("--tokenizer-ca-file", default=None)
    p.add_argument("--chunk-rows", type=int, default=1000)
    return p.parse_args(argv)


def build_cli_tokenizer(args: argparse.Namespace) -> ExternalTokenizer | None:
    """The key holder's client for the bank tokenization service, or ``None`` without a URL.

    Raises:
        TokenizerUrlRefused: the URL is plain http to another machine (value-free).
    """
    if not args.tokenizer_url:
        return None
    parts = urlsplit(args.tokenizer_url)
    if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in _LOCAL_HOSTS):
        raise TokenizerUrlRefused(
            "the tokenizer URL must be https (plain http only to this machine)"
        )
    token = (
        args.tokenizer_token_file.read_text("utf-8").strip() if args.tokenizer_token_file else ""
    )
    verify: bool | str = args.tokenizer_ca_file or True
    return HttpTokenizer(args.tokenizer_url, token=token, verify=verify, retries=1)


def _json(path: Path) -> Any:
    return json.loads(path.read_text("utf-8"))


def _default(value: Any) -> str:
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def _chunks(path: Path, size: int) -> Iterator[list[tuple[int, dict[str, Any]]]]:
    """``(line number, row)`` in chunks of ``size`` rows (blank lines skipped)."""
    chunk: list[tuple[int, dict[str, Any]]] = []
    with path.open(encoding="utf-8") as rows:
        for line_no, line in enumerate(rows, start=1):
            if not line.strip():
                continue
            chunk.append((line_no, json.loads(line)))
            if len(chunk) >= size:
                yield chunk
                chunk = []
    if chunk:
        yield chunk


def reveal_file(args: argparse.Namespace, out: TextIO) -> int:
    """Reveal every line of ``--rows`` into ``out``; returns the number of rows."""
    evidence = _json(args.evidence)
    pack = evidence.get("pack", evidence)
    snapshot = _json(args.snapshot)
    key_doc = _json(args.key)
    key = key_material_from_caller(key_doc.get("key_material", key_doc))
    shape = TableShape.model_validate(snapshot["shape"])
    anchor = next(
        (
            (c["name"], c["entity_type"])
            for c in snapshot.get("columns", [])
            if c.get("anchor") and c.get("entity_type")
        ),
        None,
    )
    engine = PrivacyEngine(
        Settings(
            _env_file=None,
            llm_provider="stub",
            ner_enabled=False,
            fpe_enabled=True,
            default_locale=args.locale,
        ),
        tokenizer=None,  # DPS's own adapter settings are never read here
    )
    policy_ref: Any = args.policy or snapshot.get("policy_name")
    if isinstance(policy_ref, str) and policy_ref.endswith(".json"):
        policy_ref = _json(Path(policy_ref))
    policy = engine.policy(policy_ref)
    kid = (pack.get("consistency") or {}).get("kid")
    if not kid:
        print(
            "warning: the evidence names no key id: a wrong key is caught only by authenticated "
            "columns",
            file=sys.stderr,
        )
    elif kid != key.kid:  # before any tokenization-service call
        raise MethodError("the key's id does not match the evidence pack's key id")
    reveal_map = pack["reveal_map"]
    tokenizer = build_cli_tokenizer(args)
    count = 0
    for chunk in _chunks(args.rows, max(1, args.chunk_rows)):
        external = (
            external_reveal_cache(
                [row for _, row in chunk], reveal_map=reveal_map, shape=shape, tokenizer=tokenizer
            )
            if tokenizer is not None
            else None
        )
        lines: list[str] = []
        for line_no, row in chunk:
            try:
                revealed = reveal_row(
                    row,
                    reveal_map=reveal_map,
                    shape=shape,
                    key=key,
                    engine=engine,
                    policy=policy,
                    anchor=anchor,
                    kid=kid,
                    external=external,
                )
            except MethodError as exc:
                raise MethodError(f"line {line_no}: {exc}") from None
            lines.append(json.dumps(revealed, default=_default, ensure_ascii=False) + "\n")
        out.write("".join(lines))
        count += len(lines)
    return count


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI: revealed rows on stdout, a count on stderr; 1 on a refused reveal."""
    args = parse_args(argv)
    try:
        n = reveal_file(args, sys.stdout)
    except (MethodError, EngineError, ValueError, KeyError, ExternalServiceError) as exc:
        readable = MethodError | ExternalServiceError | TokenizerUrlRefused  # value-free messages
        detail = str(exc) if isinstance(exc, readable) else type(exc).__name__
        print(f"reveal refused ({detail})", file=sys.stderr)
        return 1
    print(f"revealed {n} rows", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
