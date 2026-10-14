"""M5 log scan (contract §0.1, security review S13): static AST checks plus a dynamic sentinel run.

Static (offline): every log call in an M5 module is ``logger.<level>("<snake_case_event>",
<allow-listed field>=…)``; no M5 module turns a caught exception into text; service modules never
print (the operator CLIs ``keys_cli`` and ``rekey`` print by design — the one-time key, counts).

Dynamic: logging configured as in production at ``DEBUG`` (JSON; the stdlib root logger as
``configure_logging`` leaves it — never below WARNING), then sentinel values flow through the
copula (fit, sample, serialise), the fidelity report and NNDR, the tokenizer adapter failing, key
grant refusals, the evidence PDF, the policy diff, the SSRF guard and the ReDoS guard refusing,
sealing with the wrong KEK, wrapping with a bad PEM, a reveal of an unbound external token, and a
third-party library's DEBUG line (Presidio's context enhancer quotes the words around a hit, S3
secrets-logs F1). No sentinel may reach stdout, stderr, the root logger or a raised error's text.
"""

from __future__ import annotations

import ast
import io
import logging
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from dps.observability import _EVENT_NAME, _SAFE_FIELDS

ROOT = Path(__file__).resolve().parents[1]
M5 = [
    "dps/engine/synth/copula.py",
    "dps/engine/synth/constraints.py",
    "dps/engine/synth/mostly.py",
    "dps/engine/synth/linked.py",
    "dps/engine/synth/keys.py",
    "dps/engine/synth/unissued.py",
    "dps/engine/metrics.py",
    "dps/engine/transform/external.py",
    "dps/engine/detect/regex_guard.py",
    "dps/layers/connectors/tabular.py",
    "dps/layers/connectors/identifier_check.py",
    "dps/auth_db.py",
    "dps/keys_cli.py",
    "dps/tenants.py",
    "dps/audit_query.py",
    "dps/reports.py",
    "dps/layers/reports/__init__.py",
    "dps/layers/reports/pdf.py",
    "dps/policy_store.py",
    "dps/netguard.py",
    "dps/rekey.py",
    "dps/retention.py",
    "dps/llm/egress.py",
    "dps/layers/api/validation.py",
    "dps/layers/api/policy_refs.py",
    "dps/layers/api/routers/admin.py",
    "dps/layers/api/routers/reports.py",
    "dps/layers/api/routers/policy_store.py",
]
#: Operator CLIs: they print the one-time key / counts to the operator's terminal by design.
PRINTS_OK = {"dps/keys_cli.py", "dps/rekey.py"}
LEVELS = {"debug", "info", "warning", "error", "exception", "critical"}
EXC_NAMES = {"exc", "err", "error", "e", "ex"}
SENTINELS = (
    "Zebulon Quixote",
    "123-45-6789",
    "AFFLUENT-SENTINEL",
    "dps_S3NT1NELrawkey0000000000000000000000000",
    "c2VudGluZWwtc2VlZC0zMi1ieXRlcy0wMDAwMDAwMDA",
)


def _tree(rel: str) -> ast.Module:
    return ast.parse((ROOT / rel).read_text(encoding="utf-8"))


#: DPS's own refusals, whose messages name paths, counts and rules by construction: turning one
#: into a job's value-free detail is allowed (``tabular.py``: CopulaError → ``tabular_invalid``).
DOMAIN_ERRORS = {"CopulaError", "TabularUnavailable", "ConstraintError"}


def _domain_handler(handler: ast.ExceptHandler) -> bool:
    kinds = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return all(isinstance(k, ast.Name) and k.id in DOMAIN_ERRORS for k in kinds)


def _exception_text_uses(tree: ast.AST) -> list[int]:
    names = set(EXC_NAMES)
    allowed: set[int] = set()  # node ids inside handlers of DPS domain errors
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
            if node.type is not None and _domain_handler(node):
                allowed |= {id(n) for n in ast.walk(node)}

    def is_exc(node: ast.AST) -> bool:
        return isinstance(node, ast.Name) and node.id in names and id(node) not in allowed

    bad: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"str", "repr", "format"}
            and node.args
            and is_exc(node.args[0])
        ):
            bad.append(node.lineno)
        elif isinstance(node, ast.FormattedValue) and is_exc(node.value):
            bad.append(node.lineno)
        elif isinstance(node, ast.Attribute) and is_exc(node.value):
            if node.attr in {"message", "errmsg", "_message", "details"}:
                bad.append(node.lineno)
    return sorted(bad)


def test_the_static_scan_finds_what_it_must_refuse() -> None:
    bad = ast.parse(
        "try:\n    pass\nexcept ValueError as boom:\n"
        "    x = str(boom)\n"
        "    y = f'failed {boom}'\n"
        "    z = boom.message\n"
        "try:\n    pass\nexcept CopulaError as exc:\n    w = str(exc)\n"
    )
    assert _exception_text_uses(bad) == [4, 5, 6]


def test_the_scan_covers_every_m5_module() -> None:
    assert [rel for rel in M5 if not (ROOT / rel).is_file()] == []


@pytest.mark.parametrize("rel", M5)
def test_log_calls_are_allow_listed(rel: str) -> None:
    for node in ast.walk(_tree(rel)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in LEVELS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "logger"
        ):
            where = f"{rel}:{node.lineno}"
            assert node.args and isinstance(node.args[0], ast.Constant), f"{where} event"
            assert isinstance(node.args[0].value, str), f"{where} event not literal"
            assert _EVENT_NAME.fullmatch(node.args[0].value), where
            assert len(node.args) == 1, f"{where} positional format args"
            assert all(k.arg is not None for k in node.keywords), f"{where} ** splat"
            bad = [k.arg for k in node.keywords if k.arg not in _SAFE_FIELDS]
            assert not bad, f"{where} fields {bad}"


@pytest.mark.parametrize("rel", M5)
def test_no_exception_text_and_no_prints(rel: str) -> None:
    tree = _tree(rel)
    assert _exception_text_uses(tree) == [], f"{rel} turns an exception into text"
    if rel in PRINTS_OK:
        return
    prints = [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "print"
    ]
    assert prints == [], f"{rel} prints at lines {prints}"


# -- dynamic --------------------------------------------------------------------------------------


@pytest.fixture
def json_logs() -> Iterator[io.StringIO]:
    """structlog as in production (JSON, DEBUG) plus a root handler; the old config is restored.
    The root logger keeps the level ``configure_logging`` gives it (never below WARNING)."""
    import structlog

    from dps.observability import configure_logging

    old = structlog.get_config()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    old_level = root.level
    root.setLevel(logging.NOTSET)  # as in a fresh process
    configure_logging("DEBUG", json_output=True)
    root.addHandler(handler)
    try:
        yield stream
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
        structlog.configure(**old)


def _captured(json_logs: io.StringIO, capsys: pytest.CaptureFixture[str]) -> str:
    cap = capsys.readouterr()
    return json_logs.getvalue() + cap.out + cap.err


def _texts(errors: list[BaseException]) -> str:
    out: list[str] = []
    for err in errors:
        out += [str(err), repr(err), repr(err.args)]
        if err.__cause__ is not None:
            out += [str(err.__cause__), repr(err.__cause__)]
    return "\n".join(out)


def test_the_capture_sees_a_planted_sentinel(
    json_logs: io.StringIO, capsys: pytest.CaptureFixture[str]
) -> None:
    from dps.observability import get_logger

    get_logger("logscan_m5_selftest").debug("planted_leak", reason="AFFLUENT-SENTINEL-PLANTED")
    logging.getLogger("logscan_m5_stdlib").warning("stdlib %s", "AFFLUENT-SENTINEL-STDLIB")
    captured = _captured(json_logs, capsys)
    assert "AFFLUENT-SENTINEL-PLANTED" in captured and "AFFLUENT-SENTINEL-STDLIB" in captured


def test_m5_paths_never_log_or_raise_a_value(  # noqa: PLR0915 - one scan over every path
    json_logs: io.StringIO, capsys: pytest.CaptureFixture[str]
) -> None:
    import base64

    import numpy as np

    from dps.auth import Principal
    from dps.auth_db import GrantError, check_grant
    from dps.config import Settings
    from dps.engine.detect.regex_guard import UnsafePattern, check_pattern
    from dps.engine.metrics import fidelity_report, nndr
    from dps.engine.policy import load_preset
    from dps.engine.synth.copula import GaussianCopula
    from dps.engine.transform.apply import ApplyOptions, RevealHint, reveal
    from dps.engine.transform.crypto import generate_key_material, wrap_key_material
    from dps.engine.transform.external import ExternalServiceError, HttpTokenizer
    from dps.engine.transform.methods import MethodError
    from dps.layers.reports.pdf import render_evidence_pdf
    from dps.netguard import NetGuardError, check_scan_url
    from dps.policy_store import diff_policies
    from dps.secrets import SealError, open_sealed, seal

    errors: list[BaseException] = []
    seed = b"\x01" * 32
    # copula + metrics over sentinel categories
    names = ["Zebulon Quixote", "AFFLUENT-SENTINEL", "123-45-6789", "plain"] * 30
    columns = {"$.label": names, "$.amount": [float(i % 17) for i in range(120)]}
    kinds = {"$.label": "categorical", "$.amount": "float"}
    model = GaussianCopula.fit(columns, kinds, rare_floor=0.0, seed=seed, min_category_count=1)
    rows = model.sample_rows([f"row:{i}" for i in range(40)])
    blob = model.to_bytes()
    assert GaussianCopula.from_bytes(blob, seed).sample_rows(["row:0"])
    synth = {k: [r.get(k) for r in rows] for k in columns}
    fidelity_report(columns, synth, kinds, column_limit=0.05, corr_limit=0.1)
    nndr(np.arange(40.0).reshape(20, 2), np.arange(40.0).reshape(20, 2) + 0.5, sample=20)

    # the tokenizer adapter failing: neither the values nor the token reach a log or an error
    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="echo 123-45-6789 Zebulon Quixote")

    tokenizer = HttpTokenizer(
        "http://tokenizer.local",
        token="dps_S3NT1NELrawkey0000000000000000000000000",
        retries=1,
        transport=httpx.MockTransport(failing),
    )
    with pytest.raises(ExternalServiceError) as down:
        tokenizer.tokenize("SSN", ["123-45-6789", "Zebulon Quixote"], profile="AFFLUENT-SENTINEL")
    errors.append(down.value)

    # key grant refusals
    actor = Principal(key_id="k1", name="ops", tenant_ids=["ta"], scopes=["jobs"])
    for tenants, scopes in (
        (["AFFLUENT-SENTINEL"], ["jobs"]),
        (["ta"], ["admin"]),
        (["ta"], ["*"]),
    ):
        with pytest.raises(GrantError) as refused:
            check_grant(actor, tenants, scopes)
        errors.append(refused.value)

    # evidence PDF and policy diff over sentinel strings
    render_evidence_pdf(
        {"id": "e1", "job_id": "j1", "notes": "Zebulon Quixote 123-45-6789"}, pack_sha256="0" * 64
    )
    diff_policies(
        {"detect": {"custom_recognizers": [{"name": "r", "deny_list": ["Zebulon Quixote"]}]}},
        {"detect": {"custom_recognizers": [{"name": "r", "deny_list": ["AFFLUENT-SENTINEL"]}]}},
    )

    # guards refusing
    settings = Settings(_env_file=None, env="prod", llm_provider="stub")
    for url in ("http://AFFLUENT-SENTINEL.bank.internal/", "https://user:Zebulon@x.example/"):
        with pytest.raises(NetGuardError) as guarded:
            check_scan_url(url, registered_hosts=[], settings=settings)
        errors.append(guarded.value)
    with pytest.raises(UnsafePattern) as unsafe:
        check_pattern("(AFFLUENT-SENTINEL+)+$")
    errors.append(unsafe.value)

    # crypto refusal paths
    sealed = seal(b"\x02" * 32, b"Zebulon Quixote 123-45-6789", aad="dps_job.key:j1")
    with pytest.raises(SealError) as wrong_kek:
        open_sealed(b"\x03" * 32, sealed, aad="dps_job.key:j1")
    errors.append(wrong_kek.value)
    km = generate_key_material("request")
    with pytest.raises(ValueError) as bad_pem:
        wrap_key_material(
            km, "-----BEGIN PUBLIC KEY-----\nAFFLUENT-SENTINEL\n-----END PUBLIC KEY-----"
        )
    errors.append(bad_pem.value)
    options = ApplyOptions(
        policy=load_preset("gdpr-pseudonymise"),
        key=km,
        surrogates=None,
        fpe_enabled=False,
        binding_keys=(b"\x04" * 32,),
    )
    hint = RevealHint(
        path="$.ssn",
        entity_type="SSN",
        method="external_tokenize",
        reverse_params={"profile": "ssn", "bind": "A" * 26},
    )
    with pytest.raises(MethodError) as unbound:
        reveal(output={"ssn": "tok-123-45-6789"}, key=km, hints=[hint], options=options)
    errors.append(unbound.value)

    # a third-party library's DEBUG output (Presidio's context enhancer)
    logging.getLogger("presidio-analyzer").debug("Context list is: %s", "Zebulon Quixote 1971")
    logging.getLogger("some.new.library").info("payload %s", "123-45-6789")

    captured = _captured(json_logs, capsys)
    error_text = _texts(errors)
    for sentinel in (*SENTINELS, base64.b64encode(seed).decode()):
        assert sentinel not in captured, f"a sentinel reached the logs: {sentinel[:4]}…"
        assert sentinel not in error_text, f"a sentinel is in a raised error: {sentinel[:4]}…"
