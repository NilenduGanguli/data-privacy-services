"""Reveal L2 output rows with the caller's key — run **by the key holder, locally** (contract §6.2).

    python tools/reveal_rows.py --evidence pack.json --snapshot snapshot.json --key key.json \\
        --rows rows.jsonl [--locale en_IN] [--policy <preset or policy.json>] > revealed.jsonl

- ``--evidence``: the evidence pack (``GET /api/v1/evidence/{id}``'s answer or its ``pack``); its
  ``reveal_map`` says which columns reveal and with which method parameters;
- ``--snapshot``: the accepted snapshot the run used (``GET /api/v1/snapshots/{id}``): the table
  shape and the anchor column;
- ``--key``: the key material ``POST /jobs/run`` returned once (or a job group's key);
- ``--rows``: JSON lines as exported (``files`` jsonl) or selected by the operator from a target.

Each output row is revealed with :func:`dps.engine.batch.reveal_row` (a candidate is accepted only
when the forward method maps it back to the stored token, so a wrong key fails instead of
returning wrong values). ``--locale`` must be the run's locale (the policy's, else the worker's
``DPS_DEFAULT_LOCALE``; ``en_IN`` in the compose stack). DPS never stores the key and this tool
makes no network call. Errors name a line number and a path, never a value.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, TextIO

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # `python tools/reveal_rows.py`

from dps.config import Settings  # noqa: E402
from dps.engine.batch import reveal_row  # noqa: E402
from dps.engine.service import EngineError, PrivacyEngine  # noqa: E402
from dps.engine.transform.crypto import key_material_from_caller  # noqa: E402
from dps.engine.transform.methods import MethodError  # noqa: E402
from dps.layers.connectors.base import TableShape  # noqa: E402


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """The CLI (module docstring)."""
    p = argparse.ArgumentParser(prog="python tools/reveal_rows.py")
    p.add_argument("--evidence", type=Path, required=True)
    p.add_argument("--snapshot", type=Path, required=True)
    p.add_argument("--key", type=Path, required=True)
    p.add_argument("--rows", type=Path, required=True)
    p.add_argument("--locale", default="en_US")
    p.add_argument("--policy", default=None, help="a preset name or a policy JSON file")
    return p.parse_args(argv)


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
        )
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
    count = 0
    with args.rows.open(encoding="utf-8") as rows:
        for line_no, line in enumerate(rows, start=1):
            if not line.strip():
                continue
            try:
                revealed = reveal_row(
                    json.loads(line),
                    reveal_map=pack["reveal_map"],
                    shape=shape,
                    key=key,
                    engine=engine,
                    policy=policy,
                    anchor=anchor,
                    kid=kid,
                )
            except MethodError as exc:
                raise MethodError(f"line {line_no}: {exc}") from None
            out.write(json.dumps(revealed, default=_default, ensure_ascii=False) + "\n")
            count += 1
    return count


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI: revealed rows on stdout, a count on stderr; 1 on a refused reveal."""
    args = parse_args(argv)
    try:
        n = reveal_file(args, sys.stdout)
    except (MethodError, EngineError, ValueError, KeyError) as exc:
        detail = str(exc) if isinstance(exc, MethodError) else type(exc).__name__
        print(f"reveal refused ({detail})", file=sys.stderr)
        return 1
    print(f"revealed {n} rows", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
