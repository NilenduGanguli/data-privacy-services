"""SEC-5: a scan's ``auth.storage_state_ref`` follows SEC-2's tenant-scoped secret rule.

The browser-worker reads the storage state with main's resolver,
``dps.secrets.resolve_secret_ref(tenant_id, ref, settings)`` — containment inside the scan's own
tenant folder ``DPS_SECRET_DIR/<tenant>/<ref>/`` (or an allow-listed ``_shared/<ref>/``), no copy of
the tenant scoping of its own — and ``POST /scans`` checks the reference when the scan is accepted
with the rule a connection's ``secret_ref`` gets (``dps.secrets.check_secret_ref``): another
tenant's folder is 403 ``secret_ref_foreign_tenant``, an unlisted shared folder 403
``secret_ref_not_shared``, a malformed reference 422 ``secret_ref_invalid``.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from dps.config import Settings
from dps.jobs_models import ScanAuth, ScanSpec
from dps.layers.scanner import ScanSetupError, _storage_state
from dps.layers.scanner.models import ScanSpecError, validate_scan

#: The demo site's documented local session (a literal of the demo, not a secret).
STATE: dict[str, Any] = {
    "cookies": [
        {"name": "demo_session", "value": "demo-valid", "domain": "demo-site", "path": "/"}
    ],
    "origins": [],
}
HOSTS = frozenset({"demo-site"})
URL = "http://demo-site:8410/apply"


def _write(root: Path, rel: str, content: str | None = None) -> Path:
    folder = root / rel
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "storage_state.json").write_text(content or json.dumps(STATE), encoding="utf-8")
    return folder


def _settings(root: Path) -> Settings:
    return Settings(_env_file=None, env="local", secret_dir=str(root))


def _ctx(root: Path) -> Any:
    return SimpleNamespace(settings=_settings(root))


def _refused(root: Path, tenant: str, ref: str) -> str:
    with pytest.raises(ScanSetupError) as err:
        _storage_state(tenant, ref, _ctx(root))
    return err.value.code


# -- the browser-worker: main's resolver, the scan's own tenant folder only -----------------------


def test_a_scan_reads_its_storage_state_under_its_own_tenant_folder(tmp_path: Path) -> None:
    _write(tmp_path, "acme/dps/ui/demo")
    assert _storage_state("acme", "dps/ui/demo", _ctx(tmp_path)) == STATE


def test_the_storage_state_is_resolved_by_mains_resolver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No tenant scoping of the scanner's own: the tenant and the reference go to SEC-2's
    ``resolve_secret_ref`` as they are (it owns the layout, containment and the shared rule)."""
    import dps.layers.scanner as scanner

    seen: list[tuple[Any, ...]] = []

    def resolver(*args: Any, **kwargs: Any) -> dict[str, str]:
        seen.append(args + tuple(kwargs.values()))
        return {"storage_state.json": json.dumps(STATE)}

    monkeypatch.setattr(scanner, "resolve_secret_ref", resolver)
    ctx = _ctx(tmp_path)
    assert _storage_state("acme", "dps/ui/demo", ctx) == STATE
    assert seen == [("acme", "dps/ui/demo", ctx.settings)]


def test_another_tenants_storage_state_is_never_read(tmp_path: Path) -> None:
    """Cross-tenant: the same reference resolves under the scan's own tenant only, and a
    reference that names another tenant's folder is refused (not read through it)."""
    _write(tmp_path, "beta/dps/ui/demo")
    (tmp_path / "acme").mkdir()
    assert _refused(tmp_path, "acme", "dps/ui/demo") == "secret_unavailable"
    assert _refused(tmp_path, "acme", "beta/dps/ui/demo") == "secret_unavailable"
    assert _storage_state("beta", "dps/ui/demo", _ctx(tmp_path)) == STATE


def test_a_top_level_folder_that_is_not_the_tenants_is_refused(tmp_path: Path) -> None:
    """SEC-2: a reference whose first segment names an existing top-level folder other than the
    caller's tenant (the pre-scoping ``DPS_SECRET_DIR/dps/…`` layout, or another tenant) is
    refused even when the tenant's own folder holds the same path (main's demo-secrets removes
    the old top-level folder for this reason)."""
    _write(tmp_path, "dps/ui/demo")
    _write(tmp_path, "acme/dps/ui/demo")
    assert _refused(tmp_path, "acme", "dps/ui/demo") == "secret_unavailable"


def test_a_shared_storage_state_needs_the_tenant_listed(tmp_path: Path) -> None:
    folder = _write(tmp_path, "_shared/ui/demo")
    (folder / "tenants").write_text("# the tenants that may use it\nacme\n", encoding="utf-8")
    assert _storage_state("acme", "_shared/ui/demo", _ctx(tmp_path)) == STATE
    assert _refused(tmp_path, "beta", "_shared/ui/demo") == "secret_unavailable"


def test_a_symlink_out_of_the_tenant_folder_is_refused(tmp_path: Path) -> None:
    target = _write(tmp_path, "beta/dps/ui/demo")
    (tmp_path / "acme/dps/ui").mkdir(parents=True)
    (tmp_path / "acme/dps/ui/demo").symlink_to(target, target_is_directory=True)
    assert _refused(tmp_path, "acme", "dps/ui/demo") == "secret_unavailable"
    (tmp_path / "acme/dps/ui/file").mkdir()
    (tmp_path / "acme/dps/ui/file/storage_state.json").symlink_to(target / "storage_state.json")
    assert _refused(tmp_path, "acme", "dps/ui/file") == "secret_unavailable"


@pytest.mark.parametrize(
    "tenant,ref",
    [
        ("ACME", "dps/ui/demo"),
        ("_shared", "ui/demo"),
        ("acme", "/dps/ui/demo"),
        ("acme", "dps/../beta/dps/ui/demo"),
        ("acme", "dps/.hidden/demo"),
        ("acme", ""),
    ],
)
def test_malformed_tenants_and_references_are_refused(
    tmp_path: Path, tenant: str, ref: str
) -> None:
    _write(tmp_path, "acme/dps/ui/demo")
    _write(tmp_path, "beta/dps/ui/demo")
    assert _refused(tmp_path, tenant, ref) == "secret_unavailable"


def test_a_storage_state_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    _write(tmp_path, "acme/dps/ui/list", "[]")
    _write(tmp_path, "acme/dps/ui/text", "not json")
    assert _refused(tmp_path, "acme", "dps/ui/list") == "secret_unavailable"
    assert _refused(tmp_path, "acme", "dps/ui/text") == "secret_unavailable"
    (tmp_path / "acme/dps/ui/none").mkdir(parents=True)
    (tmp_path / "acme/dps/ui/none/username").write_text("u", encoding="utf-8")
    assert _refused(tmp_path, "acme", "dps/ui/none") == "secret_unavailable"


# -- POST /scans: the reference is checked for the caller's tenant when the scan is accepted ---


def test_scan_auth_takes_the_connection_secret_ref_pattern() -> None:
    """``auth.storage_state_ref`` accepts what ``ConnectionSpec.secret_ref`` accepts (a
    tenant-relative path, or ``_shared/<path>``); ``..`` stays refused by the model."""
    assert ScanAuth(storage_state_ref="_shared/ui/demo").storage_state_ref == "_shared/ui/demo"
    assert ScanAuth(storage_state_ref="dps/ui/demo").storage_state_ref == "dps/ui/demo"
    for bad in ("/dps/ui/demo", "Dps/ui", "dps/../x", "_other/ui/demo", "dps ui"):
        with pytest.raises(ValidationError):
            ScanAuth(storage_state_ref=bad)


@pytest.fixture
def secret_root(tmp_path: Path) -> Path:
    (tmp_path / "acme").mkdir()
    _write(tmp_path, "beta/dps/ui/demo")
    (_write(tmp_path, "_shared/ui/demo") / "tenants").write_text("acme\n", encoding="utf-8")
    (_write(tmp_path, "_shared/ui/other") / "tenants").write_text("beta\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    "ref,refusal",
    [
        ("dps/ui/demo", None),  # the tenant's own folder: may be mounted later, as for connections
        ("_shared/ui/demo", None),
        ("beta/dps/ui/demo", ("secret_ref_foreign_tenant", 403)),
        ("_shared/ui/other", ("secret_ref_not_shared", 403)),
        ("_shared/ui/missing", ("secret_ref_not_shared", 403)),
        ("dps/.hidden/demo", ("secret_ref_invalid", 422)),
        ("dps/_reserved/demo", ("secret_ref_invalid", 422)),
    ],
)
def test_the_reference_is_checked_for_the_callers_tenant_at_accept(
    secret_root: Path, ref: str, refusal: tuple[str, int] | None
) -> None:
    spec = ScanSpec.model_validate({"url": URL, "auth": {"storage_state_ref": ref}})
    settings = _settings(secret_root)
    if refusal is None:
        validated = validate_scan(spec, settings=settings, hosts=HOSTS, tenant_id="acme")
        assert validated.spec.auth.storage_state_ref == ref
        return
    with pytest.raises(ScanSpecError) as err:
        validate_scan(spec, settings=settings, hosts=HOSTS, tenant_id="acme")
    assert (err.value.code, err.value.status) == refusal
    assert ref not in str(err.value) and "beta" not in str(err.value)


def test_a_scan_without_a_reference_needs_no_secret_folder(tmp_path: Path) -> None:
    spec = ScanSpec.model_validate({"url": URL})
    v = validate_scan(spec, settings=_settings(tmp_path / "absent"), hosts=HOSTS, tenant_id="acme")
    assert v.spec.auth.storage_state_ref is None
