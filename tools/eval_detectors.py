"""Detector evaluation harness (spec §5.3).

    python tools/eval_detectors.py generate --seed 7 --records 150 --out tests/eval/corpus
    python tools/eval_detectors.py run --corpus tests/eval/corpus \\
        --json docs/technical/detector-eval-m1.json --markdown docs/technical/detector-eval-m1.md
    python tools/eval_detectors.py run --corpus /secure/bank-labelled.jsonl   # the bank's own slot

The bank's labelled samples use the same JSONL record shape and must never be committed
(``tests/eval/private/`` is git-ignored). Output is numbers and entity types only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dps.evaluation.corpus import generate_corpus, load_corpus, write_corpus
from dps.evaluation.scoring import evaluate, render_markdown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate", help="write the labelled synthetic corpus")
    gen.add_argument("--seed", type=int, default=7)
    gen.add_argument(
        "--records", type=int, default=150, help="records per jurisdiction (half text, half JSON)"
    )
    gen.add_argument("--out", type=Path, default=Path("tests/eval/corpus"))
    run = sub.add_parser("run", help="score the detectors against a corpus")
    run.add_argument("--corpus", type=Path, nargs="+", default=[Path("tests/eval/corpus")])
    run.add_argument("--min-score", type=float, default=0.6)
    run.add_argument(
        "--ner", action="store_true", help="enable the Presidio tier (needs the [ner] extra)"
    )
    run.add_argument("--json", type=Path)
    run.add_argument("--markdown", type=Path)
    args = parser.parse_args(argv)
    if args.command == "generate":
        for path in write_corpus(generate_corpus(args.seed, args.records), args.out):
            print(f"wrote {path}")
        return 0
    records, digest = load_corpus(args.corpus)
    report = evaluate(records, corpus_sha256=digest, min_score=args.min_score, ner=args.ner)
    if args.json:
        args.json.write_text(
            json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    markdown = render_markdown(report)
    if args.markdown:
        args.markdown.write_text(markdown, encoding="utf-8")
    sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
