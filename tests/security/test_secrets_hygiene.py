"""S2 and repository hygiene: no secret in a repr, the tree, or a dangerous construct.

``repr``/``str`` of :class:`~dps.config.Settings` hide every secret setting; the engine's
dataclasses hide the SEC-4 binding key and the external token cache (a token-to-original map);
no private key, cloud credential or raw DPS key is tracked; no ``.env`` file is tracked; no
``pickle``/``eval``/``exec``/unsafe YAML in ``dps/``; the console never injects HTML.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from dps.config import Settings
from dps.engine.policy import load_preset
from dps.engine.transform.apply import ApplyOptions
from dps.engine.transform.methods import TransformContext

ROOT = Path(__file__).resolve().parents[2]

#: Every secret setting (contract §10's literal-env refusal list, plus the local bootstrap key).
SECRET_FIELDS = (
    "api_keys",
    "bootstrap_api_key",
    "coin_client_id",
    "coin_client_secret",
    "azure_vision_key",
    "pg_password",
    "pg_worker_password",
    "pg_migration_password",
    "job_kek",
    "job_kek_previous",
    "s3_access_key_id",
    "s3_secret_access_key",
    "tokenizer_token",
)
_KEK_A = "QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE="  # base64 of 32 'A' bytes (test-only)
_KEK_B = "QkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkI="  # base64 of 32 'B' bytes (test-only)


def _tracked(*paths: str) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", *paths], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return [line for line in out.stdout.splitlines() if line]


def test_settings_repr_hides_secrets() -> None:
    values: dict[str, str] = {name: f"s3nt1nel-{i:02d}" for i, name in enumerate(SECRET_FIELDS)}
    values["job_kek"], values["job_kek_previous"] = _KEK_A, _KEK_B
    values["api_keys"] = (
        '[{"name": "s3nt1nel-key", "key": "dps_s3nt1nel", "tenant_ids": ["t"], "scopes": ["detect"]}]'
    )
    settings = Settings(_env_file=None, env="local", llm_provider="stub", **values)  # type: ignore[arg-type]
    shown = repr(settings) + str(settings)
    leaked = [name for name, value in values.items() if value in shown]
    assert leaked == []
    for name in SECRET_FIELDS:  # a field added later must be declared repr=False as well
        assert Settings.model_fields[name].repr is False, name


def test_engine_dataclasses_hide_binding_keys_and_token_maps() -> None:
    binding = b"\x07" * 32
    cache = {("SSN", "123-45-6789"): "tok-s3nt1nel"}
    options = ApplyOptions(
        policy=load_preset("gdpr-pseudonymise"),
        key=None,
        surrogates=None,
        fpe_enabled=False,
        binding_keys=(binding,),
        external_cache=cache,
    )
    context = TransformContext(
        entity_type="SSN", path="$.ssn", mode="obfuscate", binding=binding, external=cache
    )
    for shown in (repr(options), repr(context)):
        assert repr(binding) not in shown and binding.hex() not in shown
        assert "123-45-6789" not in shown and "tok-s3nt1nel" not in shown


_SECRET_SHAPES = re.compile(
    r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}"
    r"|xox[baprs]-[A-Za-z0-9-]+"
)
#: A raw DPS key. Console unit tests carry one fictitious key (``dps_FICTIONAL…``, a reviewed
#: gitleaks false positive), so their files are checked for the other shapes only.
_DPS_KEY = re.compile(r"dps_[A-Za-z0-9_-]{43}")
_BINARY = {".png", ".jpg", ".pdf", ".pptx", ".woff2", ".ico"}


def test_no_private_keys_or_cloud_credentials_in_the_tree() -> None:
    hits: list[str] = []
    for rel in _tracked("dps", "tools", "deploy", "docker", "frontend/src"):
        path = ROOT / rel
        if not path.is_file() or path.suffix in _BINARY:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if _SECRET_SHAPES.search(text):
            hits.append(rel)
        console_test = rel.startswith("frontend/src/") and ".test." in rel
        if not console_test and _DPS_KEY.search(text):
            hits.append(rel)
    assert hits == []


def test_env_files_are_not_tracked() -> None:
    assert [
        p
        for p in _tracked(".env", ".env.*", "**/.env", "**/.env.*")
        if not p.endswith(".env.example")
    ] == []


_DANGEROUS = (
    (re.compile(r"^\s*(import pickle|from pickle import)", re.M), "pickle"),
    (re.compile(r"(?<![\w.])eval\("), "eval("),
    (re.compile(r"(?<![\w.])exec\("), "exec("),
    (re.compile(r"yaml\.load\((?![^)]*SafeLoader)"), "yaml.load without SafeLoader"),
    (re.compile(r"allow_pickle\s*=\s*True"), "allow_pickle=True"),
)


def test_no_dangerous_python() -> None:
    found: list[str] = []
    for path in sorted((ROOT / "dps").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT).as_posix()
        found += [f"{rel}: {what}" for rx, what in _DANGEROUS if rx.search(text)]
        if "verify=False" in text and rel != "dps/llm/gateway.py":  # the documented COIN mint
            found.append(f"{rel}: verify=False")
    assert found == []


def test_console_never_injects_html() -> None:
    src = ROOT / "frontend" / "src"
    if not src.is_dir():
        pytest.skip("no console sources")
    bad = [
        path.relative_to(ROOT).as_posix()
        for path in src.rglob("*")
        if path.suffix in {".ts", ".tsx"}
        and re.search(r"dangerouslySetInnerHTML|\.innerHTML\s*=", path.read_text(encoding="utf-8"))
    ]
    assert bad == []
