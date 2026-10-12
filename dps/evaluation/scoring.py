"""Score detector findings against labels: precision, recall and F1 per entity type (spec §5.3).

Matching is strict on the entity type: a finding and a label match when they share a path and
their character spans overlap with IoU ≥ 0.5 (a whole-value label or finding spans the whole
leaf). A span match with a different type counts as a miss for the label's type, a false
positive for the predicted type, and one confusion entry. Reports carry numbers and type names
only — never a value.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, Field

from dps import __version__
from dps.engine.detect.ensemble import Detector, DetectorConfig
from dps.engine.detect.ner_presidio import build_ner
from dps.engine.findings import Finding
from dps.engine.jsonpath import flatten
from dps.evaluation.corpus import CorpusRecord

IOU_MATCH = 0.5
THRESHOLDS = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)


class Counts(BaseModel):
    tp: int = 0
    fp: int = 0
    fn: int = 0

    def add(self, other: Counts) -> None:
        self.tp += other.tp
        self.fp += other.fp
        self.fn += other.fn


class TypeScore(BaseModel):
    entity_type: str
    tp: int
    fp: int
    fn: int
    support: int
    precision: float | None
    recall: float | None
    f1: float | None


class EvalReport(BaseModel):
    report_version: str = "dps.detector-eval.v1"
    engine_version: str = __version__
    corpus: dict[str, Any]
    config: dict[str, Any]
    overall: dict[str, float | None]
    by_type: list[TypeScore]
    #: Spans inside free text only (text records and JSON free-text leaves): what a document,
    #: an OCR page or a notes column can be swept for, as opposed to a field its key names.
    by_type_free_text: list[TypeScore] = Field(default_factory=list)
    by_jurisdiction: dict[str, dict[str, float | int | None]]
    confusion: list[dict[str, Any]] = Field(default_factory=list)
    thresholds: list[dict[str, float | None]] = Field(default_factory=list)


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def _f1(p: float | None, r: float | None) -> float | None:
    if p is None or r is None or p + r == 0:
        return None if p is None or r is None else 0.0
    return round(2 * p * r / (p + r), 4)


def _leaf_lengths(record: CorpusRecord) -> dict[str, int]:
    if record.kind == "text":
        return {"$": len(record.text or "")}
    return {path: len(str(value)) for path, value in flatten(record.data or {})}


def _span(
    path: str, start: int | None, end: int | None, lengths: dict[str, int]
) -> tuple[int, int]:
    return (0, lengths.get(path, 0)) if start is None else (start, end or start)


def _iou(a: tuple[int, int], b: tuple[int, int]) -> float:
    inter = max(0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union else 0.0


def score_record(
    record: CorpusRecord, findings: list[Finding]
) -> tuple[dict[str, Counts], Counter[tuple[str, str]]]:
    """Per-type counts and (label type, predicted type) confusions for one record."""
    lengths = _leaf_lengths(record)
    pairs: list[tuple[float, int, int]] = []
    for li, label in enumerate(record.labels):
        ls = _span(label.path, label.start, label.end, lengths)
        for fi, finding in enumerate(findings):
            if finding.locator.path != label.path:
                continue
            iou = _iou(
                ls, _span(finding.locator.path, finding.locator.start, finding.locator.end, lengths)
            )
            if iou >= IOU_MATCH:
                pairs.append((iou, li, fi))
    counts: dict[str, Counts] = {}
    confusion: Counter[tuple[str, str]] = Counter()
    used_labels: set[int] = set()
    used_findings: set[int] = set()
    for _, li, fi in sorted(pairs, key=lambda p: -p[0]):
        if li in used_labels or fi in used_findings:
            continue
        used_labels.add(li)
        used_findings.add(fi)
        truth, predicted = record.labels[li].entity_type, findings[fi].entity_type
        if truth == predicted:
            counts.setdefault(truth, Counts()).tp += 1
        else:
            counts.setdefault(truth, Counts()).fn += 1
            counts.setdefault(predicted, Counts()).fp += 1
            confusion[(truth, predicted)] += 1
    for li, label in enumerate(record.labels):
        if li not in used_labels:
            counts.setdefault(label.entity_type, Counts()).fn += 1
    for fi, finding in enumerate(findings):
        if fi not in used_findings:
            counts.setdefault(finding.entity_type, Counts()).fp += 1
    return counts, confusion


def free_text_view(
    record: CorpusRecord, findings: list[Finding]
) -> tuple[CorpusRecord, list[Finding]]:
    """The record's span labels and the findings inside free text (spans, or a text record)."""
    labels = [label for label in record.labels if label.start is not None]
    inside = [f for f in findings if record.kind == "text" or f.locator.start is not None]
    return record.model_copy(update={"labels": labels}), inside


def _scores(by_type: dict[str, Counts]) -> list[TypeScore]:
    out = []
    for entity, c in sorted(by_type.items()):
        p, r = _ratio(c.tp, c.tp + c.fp), _ratio(c.tp, c.tp + c.fn)
        out.append(
            TypeScore(
                entity_type=entity,
                tp=c.tp,
                fp=c.fp,
                fn=c.fn,
                support=c.tp + c.fn,
                precision=p,
                recall=r,
                f1=_f1(p, r),
            )
        )
    return out


def _detect(record: CorpusRecord, detectors: dict[str, Detector]) -> list[Finding]:
    detector = detectors[record.locale]
    if record.kind == "text":
        return detector.detect(text=record.text or "").findings
    return detector.detect(data=record.data or {}).findings


def evaluate(
    records: Iterable[CorpusRecord],
    *,
    corpus_sha256: str,
    min_score: float = 0.6,
    ner: bool = False,
) -> EvalReport:
    """Run the ensemble (min score 0, filtered per threshold) over every record and score it."""
    records = list(records)
    adapter = build_ner(("en", "es"), True) if ner else None
    detectors = {
        r.locale: Detector(
            DetectorConfig(min_score=0.0, locale=r.locale, ner_enabled=adapter is not None),
            ner=adapter,
        )
        for r in records
    }
    predictions = [(r, _detect(r, detectors)) for r in records]
    by_type: dict[str, Counts] = {}
    by_text: dict[str, Counts] = {}
    by_jurisdiction: dict[str, Counts] = {}
    confusion: Counter[tuple[str, str]] = Counter()
    for record, findings in predictions:
        kept = [f for f in findings if f.score >= min_score]
        counts, conf = score_record(record, kept)
        confusion += conf
        for entity, c in counts.items():
            by_type.setdefault(entity, Counts()).add(c)
            by_jurisdiction.setdefault(record.jurisdiction, Counts()).add(c)
        for entity, c in score_record(*free_text_view(record, kept))[0].items():
            by_text.setdefault(entity, Counts()).add(c)
    total = Counts()
    for c in by_type.values():
        total.add(c)
    scores = _scores(by_type)
    micro_p, micro_r = _ratio(total.tp, total.tp + total.fp), _ratio(total.tp, total.tp + total.fn)
    f1s = [s.f1 for s in scores if s.support and s.f1 is not None]
    thresholds = []
    for t in THRESHOLDS:
        sweep = Counts()
        for record, findings in predictions:
            for c in score_record(record, [f for f in findings if f.score >= t])[0].values():
                sweep.add(c)
        tp_, tr_ = _ratio(sweep.tp, sweep.tp + sweep.fp), _ratio(sweep.tp, sweep.tp + sweep.fn)
        thresholds.append({"min_score": t, "precision": tp_, "recall": tr_, "f1": _f1(tp_, tr_)})
    jurisdictions: dict[str, dict[str, float | int | None]] = {}
    for name, c in sorted(by_jurisdiction.items()):
        jp, jr = _ratio(c.tp, c.tp + c.fp), _ratio(c.tp, c.tp + c.fn)
        jurisdictions[name] = {
            "records": sum(1 for r in records if r.jurisdiction == name),
            "precision": jp,
            "recall": jr,
            "f1": _f1(jp, jr),
        }
    return EvalReport(
        corpus={
            "sha256": corpus_sha256,
            "records": len(records),
            "labels": sum(len(r.labels) for r in records),
        },
        config={"min_score": min_score, "ner": adapter is not None, "iou": IOU_MATCH},
        overall={
            "micro_precision": micro_p,
            "micro_recall": micro_r,
            "micro_f1": _f1(micro_p, micro_r),
            "macro_f1": round(sum(f1s) / len(f1s), 4) if f1s else None,
        },
        by_type=scores,
        by_type_free_text=_scores(by_text),
        by_jurisdiction=jurisdictions,
        confusion=[
            {"label": a, "predicted": b, "count": n} for (a, b), n in sorted(confusion.items())
        ],
        thresholds=thresholds,
    )


def _fmt(value: float | int | None) -> str:
    return "—" if value is None else f"{value:.3f}" if isinstance(value, float) else str(value)


def render_markdown(report: EvalReport) -> str:
    """The committed human-readable report (numbers and type names only)."""
    lines = [
        "# Detector evaluation — M1",
        "",
        f"Corpus `{report.corpus['sha256'][:16]}…` · {report.corpus['records']} records · "
        f"{report.corpus['labels']} labels · min score {report.config['min_score']} · "
        f"NER {'on' if report.config['ner'] else 'off'} · engine {report.engine_version}",
        "",
        "Generated by `python tools/eval_detectors.py run`; synthetic corpus from "
        "`tests/eval/corpus/` (surrogate engine + seeded rule-valid generators — no real data). "
        "Strict type matching, span IoU ≥ 0.5.",
        "",
        "## Overall",
        "",
        "| micro precision | micro recall | micro F1 | macro F1 |",
        "|---|---|---|---|",
        "| "
        + " | ".join(
            _fmt(report.overall[k])
            for k in ("micro_precision", "micro_recall", "micro_f1", "macro_f1")
        )
        + " |",
        "",
        "## By entity type",
        "",
        "| entity type | support | TP | FP | FN | precision | recall | F1 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {s.entity_type} | {s.support} | {s.tp} | {s.fp} | {s.fn} | {_fmt(s.precision)} | "
        f"{_fmt(s.recall)} | {_fmt(s.f1)} |"
        for s in report.by_type
    ]
    if report.by_type_free_text:
        lines += [
            "",
            "## In free text",
            "",
            "Spans inside free text only — text records and the `notes` field of JSON records: "
            "what a document, an OCR page or a notes column is swept for. A type that appears "
            "only in the table above is found by its field name alone.",
            "",
            "| entity type | support | TP | FP | FN | precision | recall | F1 |",
            "|---|---|---|---|---|---|---|---|",
        ]
        lines += [
            f"| {s.entity_type} | {s.support} | {s.tp} | {s.fp} | {s.fn} | {_fmt(s.precision)} | "
            f"{_fmt(s.recall)} | {_fmt(s.f1)} |"
            for s in report.by_type_free_text
        ]
    lines += [
        "",
        "## By jurisdiction",
        "",
        "| jurisdiction | records | precision | recall | F1 |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {name} | {v['records']} | {_fmt(v['precision'])} | {_fmt(v['recall'])} | "
        f"{_fmt(v['f1'])} |"
        for name, v in report.by_jurisdiction.items()
    ]
    lines += [
        "",
        "## Threshold sweep (micro)",
        "",
        "| min score | precision | recall | F1 |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {t['min_score']} | {_fmt(t['precision'])} | {_fmt(t['recall'])} | {_fmt(t['f1'])} |"
        for t in report.thresholds
    ]
    if report.confusion:
        lines += ["", "## Type confusions", "", "| label | predicted | count |", "|---|---|---|"]
        lines += [f"| {c['label']} | {c['predicted']} | {c['count']} |" for c in report.confusion]
    return "\n".join(lines) + "\n"
