"""S1-M4 captures-privacy r1 B1-B3 / adaptations r1 A1-A2: three more select shapes whose drawn
values no capture masked after the M4 merge (main @ ``8ea220a``).

* **B1 / A1** — a selected option with an **empty ``label`` attribute** (``<option label="">``)
  draws its text: HTML's "label of an option" is the label attribute only when it is not empty. The
  extraction read the ``label`` IDL attribute alone (``""``), so the select drew a value it was
  neither classified nor masked by — even under "Account number" (a regression of ``40d8b34``;
  ``cbbac38`` read ``label or text`` and masked these pages).
* **B2 / A2** — ``draws_placeholder`` took a real value for a placeholder ("Choose…") whenever its
  text merely *started* with pick/please/select/choose/``--`` and no listed option was selected
  with a value (an empty value, a disabled option, an option past the first 300). The select was
  boxed, but its value was no longer swept, so its other copies on the page stayed readable.
* **B3** — a **listbox** (``size > 1`` or ``multiple``) draws every option in its box, selected or
  not. Only selected options counted as shown, so a listbox the inventory itself called
  ACCOUNT_NUMBER or PERSON_NAME was never boxed while nothing in it was selected.

Every value is made up (it stands for customer data). Each select is drawn on its own pastel
background: the plain screenshot shows the colour, a masked capture none of it. Pages are served by
a counting server (a non-GET request is a submission). The OCR tests read the masked capture, both
obfuscated captures and every screen the crawler stores against the plain screenshot as control.
"""

from __future__ import annotations

import socket
import threading
from collections import Counter
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from dps.config import Settings
from dps.engine.service import PrivacyEngine
from dps.layers.scanner.capture import shown_values
from dps.layers.scanner.classify import (
    classify_fields,
    draws_placeholder,
    sensitive_prefilled,
    shown_texts,
)
from dps.layers.scanner.dom import extract_page
from dps.layers.scanner.models import OptionFacts
from dps.layers.scanner.runtime import MemoryScanSink
from tests.scanner.helpers import field_facts, guarded_context, make_runtime
from tests.scanner.test_capture_geometry import (
    _PSM,
    _masked,
    _obfuscated,
    _open,
    _pixels,
    _plain_shot,
)

#: Made-up values standing for customer data.
ACCT = "50100234567891"  # no detector reads a bare account number
ACCT_A = "50100234500001"
ACCT_B = "50100234500077"
PAN = "AQXPZ4821K"
EMAIL = "zebulon.quixotic@example.org"
NAME = "Zebulon Quixotic"
#: real names that merely start like a placeholder ("pick…", "please…", "-- …")
PICK = "Pickering Zebulon"
PLEA = "Pleasence Quorra"
OTTO = "Ottoline Brackwater"
#: rendered text that makes the crawler take an obfuscated capture of a page (tokenised there)
PAGE_EMAIL = "tamsin.dulverton@example.org"

#: the select's background (unique on every page; pastel, so the OCR reads the text on it)
SELECT_BG = (214, 245, 255)
_CSS = (
    "body{font:22px/1.5 Arial,Helvetica,sans-serif;margin:24px;color:#111;background:#fff}"
    "select{font:22px Arial,Helvetica,sans-serif;width:560px;padding:6px 10px;"
    "background:rgb(214,245,255)}"
)
#: a customizable select (Chromium 135+): the closed select draws its button
_CUSTOM = "<style>select.cs, select.cs::picker(select){appearance:base-select}</style>"
#: a sentence that repeats a value elsewhere on the page (a name in free text is not detected)
_COPY = "<p>Last payment went to {} on Monday.</p>"


def _doc(body: str, *, head: str = "", statement: bool = False, tail: str = "") -> str:
    """A page with one select in a form (and a rendered e-mail when ``statement``)."""
    lead = f"<p>Statement for {PAGE_EMAIL}</p>" if statement else ""
    return (
        "<!doctype html><html><head><meta charset='utf-8'><title>select</title>"
        f"<style>{_CSS}</style>{head}</head><body><h2>Transfer</h2>{lead}"
        f"<form method='post' action='/pay'><p>{body}</p></form>{tail}</body></html>"
    )


def _select(label: str, name: str, options: str, *, attrs: str = "", cls: str = "") -> str:
    klass = f" class='{cls}'" if cls else ""
    return (
        f"<label for='s'>{label}</label><br>"
        f"<select id='s' name='{name}'{klass}{attrs}>{options}</select>"
    )


def _payee(first: str, *, value: str = "", extra: str = "") -> str:
    """A "Beneficiary name" select (``autocomplete=name``) drawing ``first``."""
    return _select(
        "Beneficiary name",
        "beneficiary_name",
        f"<option value='{value}' selected{extra}>{first}</option>"
        "<option value='b2'>Other payee</option>",
        attrs=" autocomplete='name'",
    )


def _options(texts: tuple[str, ...], *, selected: int | None = None, extra: str = "") -> str:
    return "".join(
        f"<option value='v{i}'{extra}{' selected' if i == selected else ''}>{t}</option>"
        for i, t in enumerate(texts)
    )


_PAYEES = (OTTO, NAME, PLEA, "Other payee")
_ACCOUNTS = (ACCT_A, ACCT, ACCT_B)
_MANY = tuple(PICK if i == 350 else f"Payee {i:03d}" for i in range(400))

#: case -> (page body, head, tail, the values the page must never show in a capture)
SHAPES: dict[str, tuple[str, str, str, dict[str, str]]] = {
    # --- B1 / A1: an empty label attribute draws the option's text ---------------------------
    "label_empty_account": (
        _select(
            "Account number",
            "account_number",
            f"<option value='a1' selected label=''>{ACCT}</option>"
            "<option value='a2' label=''>Other account</option>",
        ),
        "",
        "",
        {"acct": ACCT},
    ),
    "label_empty_pan": (
        _select(
            "Branch",
            "branch",
            f"<option value='b1' selected label=''>{PAN}</option>"
            "<option value='b2' label=''>Other branch</option>",
        ),
        "",
        "",
        {"pan": PAN},
    ),
    "label_empty_name": (_payee(OTTO, value="b1", extra=" label=''"), "", "", {"name": OTTO}),
    # a neutral label: the select is classified by the e-mail it draws (discovery semantics)
    "label_empty_email": (
        _select(
            "Choice",
            "choice",
            f"<option value='a1' selected label=''>{EMAIL}</option>"
            "<option value='x' label=''>Other</option>",
        ),
        "",
        "",
        {"email": EMAIL},
    ),
    "label_empty_email_no_value": (
        _select(
            "Choice",
            "choice",
            f"<option value='' selected label=''>{EMAIL}</option>"
            "<option value='x' label=''>Other</option>",
        ),
        "",
        "",
        {"email": EMAIL},
    ),
    # a customizable select without a button draws the selected option's label (here its text)
    "custom_label_empty_no_button": (
        _select(
            "Account number",
            "account_number",
            "<option value='a1' label=''>Savings</option>"
            f"<option value='a2' selected label=''>{ACCT}</option>",
            cls="cs",
        ),
        _CUSTOM,
        "",
        {"acct": ACCT},
    ),
    # --- B2 / A2: a real value that merely starts like a placeholder ---------------------------
    "ph_pick_copy": (_payee(PICK), "", _COPY.format(PICK), {"name": PICK}),
    "ph_please_copy": (_payee(PLEA), "", _COPY.format(PLEA), {"name": PLEA}),
    "ph_dash_copy": (_payee(f"-- {OTTO} --"), "", _COPY.format(OTTO), {"name": OTTO}),
    "ph_disabled_copy": (
        _payee(PICK, value="b9", extra=" disabled"),
        "",
        _COPY.format(PICK),
        {"name": PICK},
    ),
    "ph_over300_copy": (
        _select(
            "Beneficiary name",
            "beneficiary_name",
            _options(_MANY, selected=350),
            attrs=" autocomplete='name'",
        ),
        "",
        _COPY.format(PICK),
        {"name": PICK},
    ),
    # controls: a name that starts like no placeholder, and the same name with a value
    "ph_control_copy": (_payee(OTTO), "", _COPY.format(OTTO), {"name": OTTO}),
    "ph_valued_copy": (_payee(PICK, value="b9"), "", _COPY.format(PICK), {"name": PICK}),
    # --- B3: a listbox draws every option, selected or not -------------------------------------
    "listbox_names": (
        _select(
            "Beneficiary name",
            "beneficiary_name",
            _options(_PAYEES),
            attrs=" autocomplete='name' size='4'",
        ),
        "",
        "",
        {"otto": OTTO, "name": NAME, "plea": PLEA},
    ),
    "listbox_multiple_names": (
        _select(
            "Beneficiaries",
            "beneficiary_name",
            _options(_PAYEES),
            attrs=" autocomplete='name' multiple",
        ),
        "",
        "",
        {"otto": OTTO, "name": NAME, "plea": PLEA},
    ),
    "listbox_accounts": (
        _select("Account number", "account_number", _options(_ACCOUNTS), attrs=" size='3'"),
        "",
        "",
        {"acct": ACCT, "acct_a": ACCT_A, "acct_b": ACCT_B},
    ),
    # every option of a listbox is swept, not only the 300 it lists
    "listbox_over300_copy": (
        _select(
            "Beneficiary name",
            "beneficiary_name",
            _options(_MANY),
            attrs=" autocomplete='name' size='4'",
        ),
        "",
        _COPY.format(PICK),
        {"name": PICK},
    ),
    # the control: one option selected (the whole listbox was boxed before the fix too)
    "listbox_names_selected": (
        _select(
            "Beneficiary name",
            "beneficiary_name",
            _options(_PAYEES, selected=3),
            attrs=" autocomplete='name' size='4'",
        ),
        "",
        "",
        {"otto": OTTO, "name": NAME, "plea": PLEA},
    ),
}
#: a whitespace-only label draws nothing: what the select shows is nothing (the control of B1)
WHITESPACE = _doc(
    _select(
        "Account number",
        "account_number",
        f"<option value='a1' selected label=' '>{ACCT}</option>"
        "<option value='a2'>Other account</option>",
    )
)
#: the B1 select inside a same-origin frame
FRAMED = (
    "<!doctype html><html><head><meta charset='utf-8'><title>framed</title></head><body>"
    "<h2>Transfer</h2><iframe src='/label_empty_account' "
    "style='width:900px;height:260px;border:0'></iframe></body></html>"
)
#: every option label-less: whichever one the fill keeps or picks, the select draws an account
ALL_LABEL_EMPTY = _doc(
    _select(
        "Account number",
        "account_number",
        _options(_ACCOUNTS, selected=1, extra=" label=''"),
    )
    + "</p><p><label for='fn'>Full name</label><br>"
    "<input id='fn' name='fullName' autocomplete='name'>",
    statement=True,
)
PAGES: dict[str, str] = {
    case: _doc(body, head=head, tail=tail) for case, (body, head, tail, _) in SHAPES.items()
} | {"label_whitespace": WHITESPACE, "label_empty_framed": FRAMED}
#: the same selects on a page that also renders an e-mail, so the crawler stores an obfuscated
#: capture of it whatever it decides about the select
STATEMENT_PAGES: dict[str, str] = {
    f"{case}_statement": _doc(body, head=head, tail=tail, statement=True)
    for case, (body, head, tail, _) in SHAPES.items()
} | {"label_empty_all_options_statement": ALL_LABEL_EMPTY}
#: what each OCR'd page must never show in a capture
VALUES: dict[str, dict[str, str]] = {case: v for case, (_, _, _, v) in SHAPES.items()} | {
    "label_empty_framed": {"acct": ACCT}
}
#: pages a fix must change (each read its value in some capture before it)
DEFECTS = tuple(
    c for c in VALUES if c not in {"ph_control_copy", "ph_valued_copy", "listbox_names_selected"}
)
CONTROLS = ("ph_control_copy", "ph_valued_copy", "listbox_names_selected")
#: pages whose select (or listbox) background a masked capture must cover whole
BOXED = (
    "label_empty_account",
    "label_empty_pan",
    "label_empty_name",
    "label_empty_email",
    "label_empty_email_no_value",
    "listbox_names",
    "listbox_multiple_names",
    "listbox_accounts",
    "listbox_over300_copy",
    "listbox_names_selected",
)
CRAWLED = ("label_empty_account", "ph_pick_copy", "listbox_names", "label_empty_all_options")


@pytest.fixture
def shapes_site() -> Iterator[tuple[str, Counter[str]]]:
    """The pages on 127.0.0.1, counting every request by method and path."""
    hits: Counter[str] = Counter()
    pages = {f"/{k}": v.encode() for k, v in (PAGES | STATEMENT_PAGES).items()}

    class Site(BaseHTTPRequestHandler):
        def _count(self) -> None:
            hits[f"{self.command} {self.path.split('?')[0]}"] += 1

        def do_GET(self) -> None:  # noqa: N802 - http.server's name
            self._count()
            body = pages.get(self.path.split("?")[0], b"")
            self.send_response(200 if body else 404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            self._count()
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a: Any) -> None:
            pass

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = ThreadingHTTPServer(("127.0.0.1", port), Site)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}", hits
    finally:
        srv.shutdown()
        srv.server_close()


def _submitted(hits: Counter[str]) -> list[str]:
    return [k for k in hits if not k.startswith("GET ")]


def _payee_facts(options: list[OptionFacts], **kw: Any) -> Any:
    """A visible "Beneficiary name" select (``autocomplete=name``) with ``options``."""
    base: dict[str, Any] = {
        "key": "0:1",
        "tag": "select",
        "input_type": "select-one",
        "name": "beneficiary_name",
        "label": "Beneficiary name",
        "label_source": "label_for",
        "autocomplete": "name",
        "options": options,
    }
    return field_facts(**(base | kw))


def _opt(text: str, *, value: str = "", selected: bool = True, disabled: bool = False) -> Any:
    return OptionFacts(value=value, text=text, label="", disabled=disabled, selected=selected)


# --- B2 / A2: what a drawn placeholder is ------------------------------------------------------
def test_a_placeholder_is_a_whole_placeholder_phrase_not_a_value_that_starts_like_one() -> None:
    """A select draws its placeholder only when the whole text it draws is a short placeholder
    phrase ("Choose…", "-- Select --", "Please select") from a listed option without a value: a
    real name or e-mail that merely starts with pick/please/select/choose/``--`` is a value."""
    other = _opt("Other payee", value="b2", selected=False)
    for text in ("Choose…", "Choose", "-- Select --", "Please select", "Select one", "Pick one"):
        assert draws_placeholder(_payee_facts([_opt(text), other])), text
    # a placeholder that cannot be picked (disabled, no value) is the classic pattern
    assert draws_placeholder(_payee_facts([_opt("Choose…", disabled=True), other]))
    for text in (
        PICK,
        PLEA,
        f"-- {OTTO} --",
        "pickering.zq@example.org",
        "Select 4111 1111 1111 1111",
        "Choose Bramwell",
    ):
        assert not draws_placeholder(_payee_facts([_opt(text), other])), text
    # a disabled option with a value is a value, whatever it reads
    assert not draws_placeholder(_payee_facts([_opt(PICK, value="b9", disabled=True), other]))
    assert not draws_placeholder(_payee_facts([_opt("Choose…", value="b9", disabled=True)]))
    # an option selected past the listed ones is a value, whatever it reads
    listed = [_opt(f"Payee {n:03d}", value=f"p{n}", selected=False) for n in range(300)]
    for drawn in ([PICK], ["Choose…"]):
        assert not draws_placeholder(_payee_facts(listed, drawn=drawn)), drawn
    # what the extraction read the select draw beyond its listed options is a value too
    assert not draws_placeholder(_payee_facts([_opt("Choose…"), other], drawn=["Choose…", PICK]))


def test_a_value_that_starts_like_a_placeholder_is_swept_elsewhere(
    scan_engine: PrivacyEngine,
) -> None:
    """Every copy of a value a select draws is swept, so a name that starts like a placeholder is
    hunted for elsewhere on the page; a true placeholder ("Choose…") still is not."""
    det = scan_engine.detector(scan_engine.policy(None), "en_IN")
    other = _opt("Other payee", value="b2", selected=False)
    for text in (PICK, PLEA, f"-- {OTTO} --"):
        payee = _payee_facts([_opt(text), other])
        classes = classify_fields([payee], detector=det, min_score=0.6)
        assert classes["0:1"].entity_type == "PERSON_NAME"
        assert sensitive_prefilled([payee], classes, det) == [(payee, "PERSON_NAME")]
        assert shown_values([(payee, "PERSON_NAME")]) == [(text, "PERSON_NAME")], text
    chooser = _payee_facts([_opt("Choose…"), other])
    assert shown_values([(chooser, "PERSON_NAME")]) == []


# --- B1 / A1 and B3 as classification sees them -------------------------------------------------
def test_a_listbox_shows_every_option_selected_or_not(scan_engine: PrivacyEngine) -> None:
    """B3: a listbox draws every option in its box, so it shows all of them (``FieldFacts.drawn``
    when the extraction read it, else every listed option's label) and a listbox classified
    personal — or with a detected value among its options — is boxed whatever is selected."""
    det = scan_engine.detector(scan_engine.policy(None), "en_IN")
    payees = [_opt(n, value=f"v{i}", selected=False) for i, n in enumerate(_PAYEES)]
    listbox = _payee_facts(payees, input_type="select-one", listbox=True)
    assert shown_texts(listbox) == list(_PAYEES)
    classes = classify_fields([listbox], detector=det, min_score=0.6)
    assert classes["0:1"].entity_type == "PERSON_NAME"
    assert sensitive_prefilled([listbox], classes, det) == [(listbox, "PERSON_NAME")]
    assert not draws_placeholder(listbox)
    assert [v for v, _ in shown_values([(listbox, "PERSON_NAME")])] == list(_PAYEES)
    read = listbox.model_copy(update={"drawn": [*_PAYEES, PICK]})  # past the 300 listed
    assert [v for v, _ in shown_values([(read, "PERSON_NAME")])] == [*_PAYEES, PICK]
    # a neutral listbox is classified and boxed by a detected value among its options
    neutral = listbox.model_copy(
        update={
            "label": "Choice",
            "name": "choice",
            "autocomplete": None,
            "options": [_opt("Desk one", value="d1", selected=False)]
            + [_opt(EMAIL, value="d2", selected=False)],
        }
    )
    classes = classify_fields([neutral], detector=det, min_score=0.6)
    assert classes["0:1"].entity_type == "EMAIL"
    assert sensitive_prefilled([neutral], classes, det) == [(neutral, "EMAIL")]
    # a closed select still shows only its selected option
    closed = _payee_facts(payees[:1] + [_opt(PLEA, value="z")])
    assert shown_texts(closed) == [PLEA]


@pytest.mark.browser
async def test_the_extraction_reads_what_an_empty_label_and_a_listbox_draw(
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    shapes_site: tuple[str, Counter[str]],
) -> None:
    """``EXTRACT_JS`` reads an option with ``label=""`` as its text (HTML's label of an option),
    a whitespace-only label as what it draws (nothing), and every option of a listbox; real
    Chromium extraction then classifies and masks each select by what it draws — under a neutral
    label by the e-mail it draws, under "Account number" by its label."""
    base, hits = shapes_site
    det = scan_engine.detector(scan_engine.policy(None), "en_IN")
    seen: dict[str, tuple[list[str] | None, bool, str | None, list[str], bool]] = {}
    async with guarded_context(chromium, scan_settings, base) as (ctx, _g):
        for case in (
            "label_empty_account",
            "label_empty_email",
            "label_empty_email_no_value",
            "label_whitespace",
            "listbox_names",
            "listbox_multiple_names",
            "listbox_over300_copy",
            "label_empty_name",
        ):
            page = await _open(ctx, base, case)
            snap = await extract_page(page, iframes=True, shadow_dom=True, max_fields=400)
            (field,) = snap.fields
            classes = classify_fields(snap.fields, detector=det, min_score=0.6)
            masked = [e for _, e in sensitive_prefilled(snap.fields, classes, det)]
            seen[case] = (
                field.drawn,
                field.listbox,
                classes[field.key].entity_type,
                masked,
                draws_placeholder(field),
            )
            await page.close()
    assert seen["label_empty_account"] == (
        [ACCT],
        False,
        "ACCOUNT_NUMBER",
        ["ACCOUNT_NUMBER"],
        False,
    )
    assert seen["label_empty_email"] == ([EMAIL], False, "EMAIL", ["EMAIL"], False)
    assert seen["label_empty_email_no_value"] == ([EMAIL], False, "EMAIL", ["EMAIL"], False)
    assert seen["label_empty_name"] == ([OTTO], False, "PERSON_NAME", ["PERSON_NAME"], False)
    # a whitespace-only label draws nothing: there is nothing to show (and nothing is drawn)
    assert seen["label_whitespace"] == ([], False, "ACCOUNT_NUMBER", [], False)
    assert seen["listbox_names"] == (list(_PAYEES), True, "PERSON_NAME", ["PERSON_NAME"], False)
    assert seen["listbox_multiple_names"][1:] == (True, "PERSON_NAME", ["PERSON_NAME"], False)
    drawn = seen["listbox_over300_copy"][0]
    assert drawn is not None and len(drawn) == 400 and drawn[350] == PICK  # all, not 300
    assert _submitted(hits) == []


@pytest.mark.browser
@pytest.mark.parametrize("case", BOXED)
async def test_a_select_or_listbox_is_masked_by_what_it_draws(
    case: str,
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    shapes_site: tuple[str, Counter[str]],
) -> None:
    """B1 / B3: not one pixel of the select's (or listbox's) background is left in the masked
    capture; the plain screenshot of the same page draws it."""
    base, hits = shapes_site
    det = scan_engine.detector(scan_engine.policy(None), "en_IN")
    async with guarded_context(chromium, scan_settings, base) as (ctx, _g):
        page = await _open(ctx, base, case)
        control = await _plain_shot(page)
        masked, prefilled = await _masked(page, det)
    assert _pixels(control, SELECT_BG) > 1000, case  # the control draws the select
    assert masked is not None, case
    assert _pixels(masked, SELECT_BG) == 0, (case, prefilled)
    assert _submitted(hits) == []


@pytest.mark.browser
@pytest.mark.tesseract
@pytest.mark.parametrize("case", list(VALUES))
async def test_no_capture_reads_what_a_select_draws_or_its_copies(
    case: str,
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    shapes_site: tuple[str, Counter[str]],
) -> None:
    """B1-B3 (OCR): the plain screenshot reads every value; the masked capture and both
    obfuscated captures read none of them (before the fix each defect page read its value in
    every one of them; the controls read none before and after)."""
    from tests.scanner.test_capture_ocr import _ocr, _readable, _tesseract

    cmd = _tesseract()
    base, hits = shapes_site
    values = VALUES[case]
    keys = tuple(values)
    det = scan_engine.detector(scan_engine.policy(None), "en_IN")
    outputs: dict[str, bytes | None] = {}
    async with guarded_context(chromium, scan_settings, base) as (ctx, _g):
        page = await _open(ctx, base, case)
        control = await _plain_shot(page)
        outputs["masked"], _ = await _masked(page, det)
    for mode in ("synthesize", "obfuscate"):
        async with guarded_context(chromium, scan_settings, base) as (ctx, guard):
            page = await _open(ctx, base, case)
            res = await _obfuscated(page, det, mode, scan_engine, guard, scan_settings)
            assert res.status == "captured", (case, mode, res.reason)
            outputs[mode] = res.png
    assert _readable(_ocr(cmd, control, psm=_PSM), values, keys, strict=True) == list(keys), case
    for name, png in outputs.items():
        assert png is not None, (case, name)
        seen = _readable(_ocr(cmd, png, psm=_PSM), values, keys, strict=True)
        assert seen == [], (case, name, seen)
    assert _submitted(hits) == []


@pytest.mark.browser
@pytest.mark.tesseract
async def test_a_whitespace_label_draws_nothing(
    chromium: Any,
    scan_settings: Settings,
    shapes_site: tuple[str, Counter[str]],
) -> None:
    """The control of B1: a whitespace-only ``label`` is a label (HTML), so the select draws it
    — nothing readable — and not the option's text; the extraction reads the same."""
    from tests.scanner.test_capture_ocr import _ocr, _readable, _tesseract

    cmd = _tesseract()
    base, hits = shapes_site
    async with guarded_context(chromium, scan_settings, base) as (ctx, _g):
        page = await _open(ctx, base, "label_whitespace")
        control = await _plain_shot(page)
    seen = _readable(_ocr(cmd, control, psm=_PSM), {"acct": ACCT}, ("acct",), strict=True)
    assert seen == []
    assert _submitted(hits) == []


@pytest.mark.browser
@pytest.mark.tesseract
@pytest.mark.parametrize("mode", ["synthesize", "obfuscate"])
@pytest.mark.parametrize("case", CRAWLED)
async def test_no_screen_the_crawler_stores_reads_these_select_shapes(
    case: str,
    mode: str,
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    shapes_site: tuple[str, Counter[str]],
) -> None:
    """B1-B3 through the crawler (``ScanRunner``, both modes): no stored screen — original,
    annotated, both filled screens, the masked capture — reads what the select draws or a copy of
    it, whichever label-less option the fill keeps or picks; the plain screenshot of the page reads
    it, and the server received only GETs."""
    from dps.layers.scanner.crawler import ScanRunner
    from tests.scanner.test_capture_ocr import _ocr, _readable, _tesseract

    cmd = _tesseract()
    base, hits = shapes_site
    path = f"/{case}_statement"
    if case == "label_empty_all_options":
        values = {"acct": ACCT, "acct_a": ACCT_A, "acct_b": ACCT_B}
        shown: tuple[str, ...] = ("acct",)  # a closed select draws its selected option only
    else:
        values = VALUES[case]
        shown = tuple(values)
    keys = tuple(values)
    sink = MemoryScanSink()
    runtime = make_runtime(
        base, scan_settings, scan_engine, path=path, max_depth=0, mode=mode, cookies=False
    )
    await ScanRunner(
        settings=scan_settings, engine=scan_engine, sink=sink, browser=chromium, runtime=runtime
    ).run()
    page = sink.pages[1]
    expected = {"original", "annotated", "masked_capture"}
    if case in ("label_empty_account", "label_empty_all_options"):  # fields the fill can fill
        expected |= {"filled_synthesize", "filled_obfuscate"}
    assert set(page.artefacts) >= expected, (page.status, sorted(page.artefacts))
    screens = {"original", "annotated", "filled_synthesize", "filled_obfuscate", "masked_capture"}
    for variant in sorted(set(page.artefacts) & screens):
        _, png, _ = sink.artefacts[page.artefacts[variant]]
        seen = _readable(_ocr(cmd, png, psm=_PSM), values, keys, strict=True)
        assert seen == [], (case, mode, variant, seen)
    assert _submitted(hits) == []
    async with guarded_context(chromium, scan_settings, base) as (ctx, _g):
        control = await _plain_shot(await _open(ctx, base, path.lstrip("/")))
    assert _readable(_ocr(cmd, control, psm=_PSM), values, keys, strict=True) == list(shown)
