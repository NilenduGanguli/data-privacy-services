"""Evidence-pack and API roll-up PDFs (reportlab platypus, built-in Helvetica — no fonts shipped,
no network). Every string from a pack goes through :func:`pdf_text`: XML-escaped, because
``Paragraph`` parses mini-markup and an unescaped name such as ``<link href=...>`` would become a
live link, and folded to Latin-1 because the standard fonts have no glyphs beyond it. The PDF
carries only what the pack carries: counts, hashes, names, paths, methods — never ``reveal_map``.

The renderer never trusts the pack's shape: a missing key renders as "—" and a value that is not
a number where a number is expected renders as text, so an older or partial pack still prints.
Sections follow mockup 10 (same order and headings).
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from dps import __version__

DASH = "—"
_FOLD = {
    "→": "->",
    "≥": ">=",
    "≤": "<=",
    "−": "-",
    "–": "-",
    "—": "-",
    "…": "...",
    "×": "x",
    "Δ": "delta",
    "⁶": "^6",
    "✓": "ok",
    "✕": "x",
    " ": " ",
    " ": " ",
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
}
_STYLES = getSampleStyleSheet()
EYEBROW = ParagraphStyle(
    "eyebrow",
    parent=_STYLES["BodyText"],
    fontName="Helvetica-Bold",
    fontSize=7.5,
    textColor=colors.HexColor("#0f766e"),
)
H1 = ParagraphStyle(
    "h1", parent=_STYLES["Heading1"], fontName="Helvetica-Bold", fontSize=15, spaceAfter=4
)
H2 = ParagraphStyle(
    "h2",
    parent=_STYLES["Heading2"],
    fontName="Helvetica-Bold",
    fontSize=11,
    spaceBefore=10,
    spaceAfter=4,
)
BODY = ParagraphStyle(
    "body", parent=_STYLES["BodyText"], fontName="Helvetica", fontSize=8.5, leading=11
)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=7.5, textColor=colors.HexColor("#4a4a4a"))
MONO = ParagraphStyle("mono", parent=BODY, fontName="Courier", fontSize=7.5)
_GRID = TableStyle(
    [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 7.5),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.black),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]
)
_PARAMS_MAX = 120
REGULATORY = {
    "obfuscate": (
        "The target holds pseudonymised data: values were replaced by keyed, consistent "
        "transformations, and anyone holding the job's key material can reverse the reversible "
        "methods. Pseudonymised data is still personal data (GDPR Art. 4(5), DPDP Act 2023); the "
        "target must be protected and governed as such."
    ),
    "synthesize": (
        "The target holds synthetic data generated from a statistical model of the source. The "
        "fidelity and privacy figures in this pack are measurements on this run, not a claim that "
        "the data is anonymous: decide on its release with the measured exact-row matches, "
        "nearest-neighbour distances and quasi-identifier uniqueness in view."
    ),
}


def _fold(value: Any) -> str:
    """Latin-1 text for the standard fonts (no markup escaping: canvas strings)."""
    text = "" if value is None else str(value)
    for src, dst in _FOLD.items():
        text = text.replace(src, dst)
    return text.encode("latin-1", "replace").decode("latin-1")


def pdf_text(value: Any) -> str:
    """XML-escaped, Latin-1-folded text for a ``Paragraph``."""
    return escape(_fold(value))


def _p(value: Any, style: ParagraphStyle = BODY) -> Paragraph:
    return Paragraph(pdf_text(DASH if value is None else value), style)


def _num(value: Any, spec: str = "", suffix: str = "") -> str:
    """``value`` formatted with ``spec`` when it is a number, else as text (``—`` if missing)."""
    if value is None or value == "":
        return DASH
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int | float):
        return f"{value:{spec}}{suffix}"
    return str(value)


def _pct(value: Any) -> str:
    return _num(value * 100 if isinstance(value, int | float) else value, ".1f", "%")


def _sub(pack: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = pack.get(key)
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _table(header: Sequence[str], rows: Sequence[Sequence[Any]], widths: Sequence[float]) -> Table:
    data = [[_p(h, SMALL) for h in header]] + [[_p(c) for c in row] for row in rows]
    table = Table(data, colWidths=[w * mm for w in widths], repeatRows=1)
    table.setStyle(_GRID)
    return table


def _kv(rows: Sequence[tuple[str, Any]]) -> Table:
    table = Table([[_p(k, SMALL), _p(v)] for k, v in rows], colWidths=[42 * mm, 128 * mm])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.grey),
            ]
        )
    )
    return table


def _section(n: int | None, title: str, *flow: Any) -> list[Any]:
    heading = _p(f"{n}  {title}" if n is not None else title, H2)
    return [KeepTogether([heading, *flow[:1]]), *flow[1:]]


def _target_text(pack: Mapping[str, Any]) -> str:
    target = _sub(pack, "target")
    mode = target.get("write_mode")
    if mode == "in_place":
        return "in place"
    if mode == "files" or isinstance(target.get("files"), Mapping):
        return "files"
    return str(target.get("object") or DASH)


def _header(pack: Mapping[str, Any], sha: str) -> list[Any]:
    source = _sub(pack, "source")
    verification = _sub(pack, "verification")
    passed = verification.get("passed")
    seal = "passed" if passed is True else ("failed" if passed is False else DASH)
    title = f"{source.get('engine') or DASH} {source.get('object') or DASH} → {_target_text(pack)}"
    return [
        _p("Data Privacy Services · evidence pack", EYEBROW),
        _p(title, H1),
        _p(
            f"Job {pack.get('job_id') or DASH} · {pack.get('job_kind') or DASH} · "
            f"created {pack.get('created_at') or DASH}",
            SMALL,
        ),
        _p(
            f"Verification {seal} · sealed {pack.get('created_at') or DASH} · "
            f"pack {pack.get('id') or DASH} · sha256 {sha[:16]}…",
            SMALL,
        ),
        Spacer(1, 3 * mm),
    ]


def _meta(pack: Mapping[str, Any]) -> list[Any]:
    purpose, target = _sub(pack, "purpose"), _sub(pack, "target")
    return [
        _kv(
            [
                ("Tenant", pack.get("tenant_id")),
                ("Requested by (key id)", pack.get("requested_by")),
                ("Purpose ticket", purpose.get("ticket")),
                ("Legal basis", purpose.get("legal_basis")),
                ("Job group", pack.get("group_id")),
                (
                    "Mode · write mode",
                    f"{pack.get('mode') or DASH} · {target.get('write_mode') or DASH}",
                ),
                ("Outcome", pack.get("outcome")),
            ]
        )
    ]


def _summary(pack: Mapping[str, Any]) -> list[Any]:
    counts, verification = _sub(pack, "counts"), _sub(pack, "verification")
    timings = _sub(pack, "timings")
    columns = _list(pack.get("columns"))
    rows = counts.get("rows_written")
    hits = verification.get("hits")
    sentence = (
        f"{_num(rows, ',')} rows were written to {_target_text(pack)} with "
        f"{len(columns)} column{'' if len(columns) == 1 else 's'} transformed ({_num(counts.get('values_transformed'), ',')} values); "
        f"the leakage sweep found {_num(hits, ',')} source values in the output; no raw value was "
        "sent to a language model."
    )
    cells = [
        ("Rows written", _num(rows, ",")),
        ("Columns transformed", str(len(columns))),
        ("Values transformed", _num(counts.get("values_transformed"), ",")),
        ("Leakage hits", _num(hits, ",")),
        ("Duration", _num(timings.get("duration_s"), ".1f", " s")),
        ("Raw values to LLM", "0"),
    ]
    stats = _table([c[0] for c in cells], [[c[1] for c in cells]], [28] * 6)
    return _section(1, "Summary", _p(sentence), Spacer(1, 2 * mm), stats)


def _configuration(pack: Mapping[str, Any]) -> list[Any]:
    policy, consistency = _sub(pack, "policy"), _sub(pack, "consistency")
    custody = consistency.get("key_custody")
    custody_text = (
        ", ".join(
            f"{k} {v}" for k, v in sorted(custody.items()) if not isinstance(v, Mapping | list)
        )
        if isinstance(custody, Mapping)
        else custody
    )
    source, target = _sub(pack, "source"), _sub(pack, "target")
    version = policy.get("version")
    return _section(
        2,
        "Configuration",
        _kv(
            [
                (
                    "Policy",
                    f"{policy.get('name') or DASH}{' v' + str(version) if version else ''} · "
                    f"hash {policy.get('hash') or DASH}",
                ),
                ("Config hash", pack.get("config_hash")),
                (
                    "Engine",
                    f"{pack.get('engine_version') or DASH} · image {pack.get('image_digest') or DASH}",
                ),
                (
                    "Key",
                    f"kid {consistency.get('kid') or DASH} · scope {consistency.get('scope') or DASH}"
                    f" · custody {custody_text or 'held by the caller'}",
                ),
                ("Source", f"{source.get('engine') or DASH} {source.get('object') or DASH}"),
                ("Target", f"{_target_text(pack)} ({target.get('write_mode') or DASH})"),
                ("Job group", pack.get("group_id")),
            ]
        ),
    )


def _params(value: Any) -> str:
    if not isinstance(value, Mapping) or not value:
        return ""
    text = json.dumps(value, sort_keys=True, default=str)
    return text if len(text) <= _PARAMS_MAX else text[: _PARAMS_MAX - 3] + "..."


def _snapshot(pack: Mapping[str, Any], snapshot: Mapping[str, Any] | None) -> list[Any]:
    snap = snapshot if snapshot is not None else _sub(pack, "snapshot")
    rows = []
    for c in _list(pack.get("columns")):
        if not isinstance(c, Mapping):
            continue
        method = str(c.get("method") or DASH)
        params = _params(c.get("params"))
        rows.append(
            [
                c.get("path"),
                c.get("data_type"),
                c.get("entity_type"),
                _num(c.get("confidence"), ".2f"),
                f"{method} {params}".strip(),
                "overridden" if c.get("override") else "accepted",
            ]
        )
    line = (
        f"Snapshot {snap.get('id') or DASH} · content hash {snap.get('content_hash') or DASH} · "
        f"accepted by {snap.get('accepted_by') or DASH} at {snap.get('accepted_at') or DASH} · "
        f"{_num(snap.get('overrides'))} overrides"
    )
    table = _table(
        ["Path", "Type", "Entity", "Confidence", "Method + params", "Decision"],
        rows or [[DASH] * 6],
        [36, 26, 28, 18, 44, 18],
    )
    return _section(3, "Classification snapshot", _p(line, SMALL), Spacer(1, 2 * mm), table)


def _execution(pack: Mapping[str, Any]) -> list[Any]:
    parts = [p for p in _list(pack.get("partitions")) if isinstance(p, Mapping)]
    rows = [
        [
            _num(p.get("no")),
            _num(p.get("rows"), ","),
            _num(p.get("attempts")),
            _num(p.get("resumed")),
            p.get("state"),
        ]
        for p in parts
    ]

    def total(key: str) -> Any:
        values = [p.get(key) for p in parts]
        nums = [v for v in values if isinstance(v, int | float) and not isinstance(v, bool)]
        return sum(nums) if nums else None

    rows.append(
        ["Total", _num(total("rows"), ","), _num(total("attempts")), _num(total("resumed")), ""]
    )
    timings = _sub(pack, "timings")
    line = (
        f"Started {timings.get('started_at') or DASH} · finished {timings.get('finished_at') or DASH}"
        f" · {_num(timings.get('duration_s'), '.1f', ' s')} · "
        f"{_num(timings.get('rows_per_s'), ',.1f')} rows/s"
    )
    table = _table(
        ["Partition", "Rows", "Attempts", "Resumed", "State"], rows, [24, 34, 24, 24, 64]
    )
    return _section(4, "Execution", table, Spacer(1, 2 * mm), _p(line, SMALL))


def _verification(pack: Mapping[str, Any]) -> list[Any]:
    verification, counts = _sub(pack, "verification"), _sub(pack, "counts")
    passed = verification.get("passed")
    fallbacks = verification.get("fallbacks")
    fallback_text = (
        ", ".join(f"{k} {v}" for k, v in sorted(fallbacks.items())) or "none"
        if isinstance(fallbacks, Mapping)
        else fallbacks
    )
    rows: list[tuple[str, Any]] = [
        ("Mode", verification.get("mode")),
        (
            "Leakage sweep",
            f"{'passed' if passed is True else 'failed' if passed is False else DASH} · "
            f"{_num(verification.get('hits'), ',')} source values found in the output",
        ),
        ("Detectors re-run on output", _num(verification.get("values_checked"), ",", " values")),
        ("FPE fallbacks", fallback_text),
        (
            "Row counts",
            f"read {_num(counts.get('rows_read'), ',')} · written {_num(counts.get('rows_written'), ',')}"
            f" · skipped {_num(counts.get('rows_skipped'), ',')}",
        ),
    ]
    if verification.get("review_required"):
        cols = ", ".join(str(c) for c in _list(verification.get("review_columns")))
        rows.append(("Review required", cols or "yes"))
    return _section(5, "Verification", _kv(rows))


def _column_rows(report: Mapping[str, Any]) -> tuple[list[list[str]], float]:
    limits = report.get("limits") if isinstance(report.get("limits"), Mapping) else {}
    limit = limits.get("column", 0.05) if isinstance(limits, Mapping) else 0.05
    rows = []
    for c in _list(report.get("columns")):
        if not isinstance(c, Mapping):
            continue
        delta = c.get("null_rate_delta")
        rows.append(
            [
                str(c.get("path") or DASH),
                str(c.get("metric") or DASH).upper(),
                _num(c.get("distance"), ".3f"),
                _num(limit, ".2f"),
                _num(delta * 100 if isinstance(delta, int | float) else delta, "+.1f", " pp"),
            ]
        )
    return rows or [[DASH] * 5], limit if isinstance(limit, int | float) else 0.05


def _corr(report: Mapping[str, Any]) -> list[Any]:
    matrix = report.get("corr_matrix") if isinstance(report.get("corr_matrix"), Mapping) else {}
    names = [str(n) for n in _list(matrix.get("columns"))] if isinstance(matrix, Mapping) else []
    delta = _list(matrix.get("delta")) if isinstance(matrix, Mapping) else []
    if len(names) < 2 or len(delta) < len(names):
        return []

    def cell(i: int, j: int) -> str:
        row = delta[i] if isinstance(delta[i], list) else []
        return _num(row[j], ".3f") if j < i and j < len(row) else ""

    rows = [[names[i]] + [cell(i, j) for j in range(len(names) - 1)] for i in range(1, len(names))]
    width = 120 / max(1, len(names) - 1)
    return [
        Spacer(1, 3 * mm),
        _p("Correlation-matrix delta |synthetic - source|", SMALL),
        _table([""] + names[:-1], rows, [40] + [width] * (len(names) - 1)),
    ]


def _fidelity_table(report: Mapping[str, Any], label: str | None = None) -> list[Any]:
    rows, _ = _column_rows(report)
    flow: list[Any] = []
    if label:
        flow.append(
            _p(
                f"Table {label}: score {_num(report.get('score'), '.2f')}, mean correlation delta "
                f"{_num(report.get('corr_delta'), '.3f')}",
                SMALL,
            )  # fmt: skip
        )
    flow += [
        Spacer(1, 2 * mm),
        _table(
            ["Column", "Metric", "Distance", "Limit", "Null rate delta"], rows, [60, 18, 22, 18, 30]
        ),
    ]
    return flow + _corr(report)


def _mia_row(mia: Mapping[str, Any]) -> list[str]:
    """Membership-inference proxy (Task 6B): distance-based AUC, 0.5 = no membership signal."""
    if not mia.get("run"):
        return ["Membership-inference proxy", "not run", str(mia.get("reason") or "")]
    return [
        "Membership-inference proxy",
        f"AUC {_num(mia.get('auc'), '.2f')}",
        f"target <= {_num(mia.get('target'), '.2f')}: "
        f"{'pass' if mia.get('passed') else 'above target'}; {_num(mia.get('members'))} fit rows vs "
        f"{_num(mia.get('non_members'))} holdout rows",
    ]


def _utility_row(util: Mapping[str, Any]) -> list[str]:
    """Train-on-synthetic / test-on-real for the nominated label column (Task 6B)."""
    if not util.get("run"):
        return ["Utility (TSTR)", "not run", str(util.get("reason") or "")]
    return [
        "Utility (TSTR)",
        f"{util.get('metric') or ''} {_num(util.get('train_on_synthetic'), '.3f')}",
        f"label {util.get('label') or DASH}; train-on-real {_num(util.get('train_on_real'), '.3f')}; "
        f"ratio {_num(util.get('ratio'), '.2f')} ({util.get('model') or DASH})",
    ]


def _scalars(value: Any) -> str:
    if not isinstance(value, Mapping):
        return _num(value)
    return ", ".join(
        f"{k} {_num(v)}" for k, v in sorted(value.items()) if not isinstance(v, Mapping | list)
    )


def _privacy_rows(pack: Mapping[str, Any]) -> list[list[str]]:
    priv, tabular = _sub(pack, "privacy"), _sub(pack, "tabular")
    nn = _sub(priv, "nndr")
    qi = _sub(priv, "qi_uniqueness")
    rows = [
        [
            "Exact-row matches",
            _num(priv.get("exact_row_matches")),
            "enforced: matching rows are redrawn before writing",
        ],
        [
            "Rows redrawn",
            f"{_num(priv.get('exact_row_resamples'))} (preview) / "
            f"{_num(priv.get('exact_row_resamples_run'))} (run)",
            "",
        ],
        [
            "Nearest-neighbour distance ratio",
            _num(nn.get("ratio"), ".2f"),
            f"median synthetic->real {_num(nn.get('synth_to_real_median'), '.3f')} vs real->real "
            f"{_num(nn.get('real_to_real_median'), '.3f')}; target {_num(nn.get('target'), '.2f')}: "
            f"{'pass' if nn.get('passed') else 'below target'}",
        ],
        [
            "Quasi-identifier uniqueness",
            f"{_pct(qi.get('synthetic'))} (source {_pct(qi.get('source'))})",
            f"{_num(qi.get('unique_overlap'))} unique synthetic rows match a unique source row",
        ],
        _mia_row(_sub(priv, "membership_inference")),
    ]
    util = _sub(pack, "utility")
    if util:
        rows.append(_utility_row(util))
    dp = priv.get("dp") if isinstance(priv.get("dp"), Mapping) else tabular.get("dp")
    if isinstance(dp, Mapping):
        rows.append(["Differential privacy", "on" if dp.get("enabled") else "off", _scalars(dp)])
    return rows


def _fidelity(pack: Mapping[str, Any]) -> list[Any]:
    fid, priv, tabular = _sub(pack, "fidelity"), _sub(pack, "privacy"), _sub(pack, "tabular")
    if not fid:
        if priv.get("engine") == "none":
            return _section(
                6,
                "Synthetic data",
                _p(
                    "Privacy not measured: non-sensitive columns were copied from the source "
                    "(options.tabular=none)."
                ),
            )
        return []
    measured = _sub(fid, "measured_on")
    flow: list[Any] = [
        _p(
            f"Engine {fid.get('engine') or 'copula'}; summary score {_num(fid.get('score'), '.2f')} "
            f"(1 - mean column distance - mean correlation delta {_num(fid.get('corr_delta'), '.3f')})."
            f" Measured on {_scalars(measured) or DASH}."
        )
    ]
    tables = fid.get("tables")
    if isinstance(tables, Mapping) and tables:  # MOSTLY linked generation (Task 7B)
        for name, report in tables.items():
            if isinstance(report, Mapping):
                flow += _fidelity_table(report, str(name))
    else:
        flow += _fidelity_table(fid)
    linked = tabular.get("linked")
    if isinstance(linked, Mapping):
        flow += [Spacer(1, 2 * mm), _p(f"Linked generation: {_scalars(linked)}", SMALL)]
    flow += [
        Spacer(1, 3 * mm),
        _table(["Privacy check", "Result", "What it means"], _privacy_rows(pack), [45, 35, 90]),
    ]
    return _section(6, "Synthetic data — fidelity and privacy", *flow)


def _scan(pack: Mapping[str, Any]) -> list[Any]:
    scan = _sub(pack, "scan")
    if not scan:
        return []
    blocked = scan.get("blocked_requests")
    blocked_text = (
        ", ".join(f"{k} {_num(v)}" for k, v in sorted(blocked.items())) or "none"
        if isinstance(blocked, Mapping)
        else _num(blocked)
    )
    return _section(
        None,
        "UI scan",
        _kv(
            [
                ("Pages", _num(scan.get("pages"))),
                ("Forms", _num(scan.get("forms"))),
                ("Fields", _num(scan.get("fields"))),
                ("Personas", _num(scan.get("personas"))),
                ("Submissions", "0 (no form was ever submitted)"),
                ("Blocked requests", blocked_text),
            ]
        ),
    )


def _regulatory(pack: Mapping[str, Any]) -> list[Any]:
    label = str(pack.get("label") or "")
    synthetic = pack.get("mode") == "synthesize" or label.startswith("synthetic")
    text = REGULATORY["synthesize" if synthetic else "obfuscate"]
    return _section(7, "Regulatory note", _p(f"Label: {label or DASH}.", SMALL), _p(text))


def _signatures(pack: Mapping[str, Any], sha: str, snapshot: Mapping[str, Any] | None) -> list[Any]:
    snap = snapshot if snapshot is not None else _sub(pack, "snapshot")
    attestation = (
        f"System attestation: Data Privacy Services {pack.get('engine_version') or __version__} "
        f"sealed this pack at {pack.get('created_at') or DASH}; pack sha256 {sha}."
    )
    table = _table(
        ["Role", "Name", "Signature · date"],
        [
            ["Data owner", snap.get("accepted_by") or "", ""],
            ["Data protection officer", "", ""],
            ["Change approver", "", ""],
        ],
        [50, 60, 60],
    )
    table.setStyle(TableStyle([("BOTTOMPADDING", (0, 1), (-1, -1), 14)]))
    return _section(8, "Signatures", _p(attestation, SMALL), Spacer(1, 2 * mm), table)


def _footer(left: str, right: str) -> Callable[[Any, Any], None]:
    def draw(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 6.5)
        canvas.drawString(15 * mm, 10 * mm, _fold(f"{left} · page {doc.page}"))
        canvas.drawRightString(195 * mm, 10 * mm, _fold(right))
        canvas.restoreState()

    return draw


def _document(buffer: io.BytesIO, title: str) -> SimpleDocTemplate:
    return SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=18 * mm,
        title=title,
        author=f"Data Privacy Services {__version__}",
    )


def render_evidence_pdf(
    pack: Mapping[str, Any], *, pack_sha256: str, snapshot: Mapping[str, Any] | None = None
) -> bytes:
    """The printable evidence pack (mockup 10); ``snapshot`` overrides the pack's snapshot block."""
    buffer = io.BytesIO()
    doc = _document(buffer, "DPS evidence pack")
    story: list[Any] = [
        *_header(pack, pack_sha256),
        *_meta(pack),
        *_summary(pack),
        *_configuration(pack),
        *_snapshot(pack, snapshot),
        *_execution(pack),
        *_verification(pack),
        *_fidelity(pack),
        *_scan(pack),
        *_regulatory(pack),
        *_signatures(pack, pack_sha256, snapshot),
    ]
    footer = _footer(
        f"DPS evidence pack · job {pack.get('job_id') or DASH}", f"pack sha256 {pack_sha256}"
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def render_rollup_pdf(doc: Mapping[str, Any]) -> bytes:
    """One page: an API roll-up document (counts, outcomes, entity types, its sha256)."""
    buffer = io.BytesIO()
    pdf = _document(buffer, "DPS API roll-up")
    by_outcome = doc.get("by_outcome") if isinstance(doc.get("by_outcome"), Mapping) else {}
    entities = doc.get("entities") if isinstance(doc.get("entities"), Mapping) else {}
    story: list[Any] = [
        _p("Data Privacy Services · API roll-up", EYEBROW),
        _p(f"API roll-up · {doc.get('action') or DASH} · {doc.get('day') or DASH}", H1),
        _p(
            f"Generated {doc.get('generated_at') or DASH} · sha256 {doc.get('sha256') or DASH}",
            SMALL,
        ),
        Spacer(1, 3 * mm),
        _kv(
            [
                ("Calls", _num(doc.get("calls"), ",")),
                ("Findings", _num(doc.get("findings"), ",")),
                ("Values transformed", _num(doc.get("values"), ",")),
                ("Verification passed", _num(doc.get("verify_passed"), ",")),
                ("Verification failed", _num(doc.get("verify_failed"), ",")),
            ]
        ),
        _p("Outcomes", H2),
        _table(
            ["Outcome", "Calls"],
            [[k, _num(v, ",")] for k, v in sorted((by_outcome or {}).items())] or [[DASH, DASH]],
            [85, 85],
        ),
        _p("Findings by entity type (the day's API calls)", H2),
        _table(
            ["Entity type", "Findings"],
            [[k, _num(v, ",")] for k, v in sorted((entities or {}).items())] or [[DASH, DASH]],
            [85, 85],
        ),
    ]
    footer = _footer(
        f"DPS API roll-up · {doc.get('action') or DASH} · {doc.get('day') or DASH}",
        f"sha256 {doc.get('sha256') or DASH}",
    )
    pdf.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
