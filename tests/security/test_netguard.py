"""S5: a scan target is checked before any browser starts (numeric encodings, IPv4-mapped IPv6,
metadata addresses, DNS that resolves to link-local, loopback outside local), and Chromium
resolves only the registered hosts."""

from __future__ import annotations

import ipaddress

import pytest

from dps.config import Settings
from dps.netguard import (
    NetGuardError,
    check_scan_url,
    chromium_resolver_rules,
    forbidden_ip_reason,
    normalize_host,
)

REGISTERED = [
    "onboarding-uat.bank.internal",
    "demo-site",
    "10.20.30.40",
    "127.0.0.1",
    "rebind.bank.internal",
    normalize_host("[::ffff:169.254.169.254]"),
]
DNS = {
    "onboarding-uat.bank.internal": ["10.1.2.3"],
    "demo-site": ["172.18.0.5"],
    "rebind.bank.internal": ["169.254.169.254"],
}


def _resolve(host: str) -> list[str]:
    if host not in DNS:
        raise OSError("no such host")
    return DNS[host]


def _settings(**kw: object) -> Settings:
    base: dict[str, object] = {"_env_file": None, "env": "local", "llm_provider": "stub"}
    base.update(kw)
    return Settings(**base)  # type: ignore[arg-type]


def test_the_ipv4_mapped_literal_is_normalised() -> None:
    canonical = str(ipaddress.ip_address("::ffff:169.254.169.254"))
    assert normalize_host("[::ffff:169.254.169.254]") == canonical
    assert normalize_host(canonical) == canonical  # a bare IPv6 literal is a host too


@pytest.mark.parametrize(
    "url,host",
    [
        ("https://onboarding-uat.bank.internal/apply", "onboarding-uat.bank.internal"),
        ("http://demo-site:8410/", "demo-site"),
        ("http://10.20.30.40/", "10.20.30.40"),
        ("http://127.0.0.1:8410/", "127.0.0.1"),  # local only
    ],
)
def test_allowed(url: str, host: str) -> None:
    assert (
        check_scan_url(url, registered_hosts=REGISTERED, settings=_settings(), resolver=_resolve)
        == host
    )


@pytest.mark.parametrize(
    "url,settings_kw",
    [
        ("ftp://demo-site/", {}),
        ("https://user:pw@demo-site/", {}),
        ("http://0x7f000001/", {}),
        ("http://2130706433/", {}),
        ("http://127.1/", {}),
        ("http://017700000001/", {}),
        ("http://[::ffff:169.254.169.254]/", {}),
        ("https://rebind.bank.internal/", {}),
        ("http://127.0.0.1/", {"env": "prod", "egress_tls_required": False}),
        ("http://demo-site/", {"egress_tls_required": True}),
    ],
)
def test_refused_as_forbidden(url: str, settings_kw: dict[str, object]) -> None:
    with pytest.raises(NetGuardError) as caught:
        check_scan_url(
            url, registered_hosts=REGISTERED, settings=_settings(**settings_kw), resolver=_resolve
        )
    assert caught.value.code == "host_forbidden"
    assert "pw" not in caught.value.detail and "user" not in caught.value.detail


def test_an_unregistered_host_is_refused() -> None:
    with pytest.raises(NetGuardError) as caught:
        check_scan_url(
            "https://evil.example/",
            registered_hosts=REGISTERED,
            settings=_settings(),
            resolver=_resolve,
        )
    assert caught.value.code == "host_not_registered"
    assert "evil" not in caught.value.detail


def test_a_registered_host_that_does_not_resolve_is_refused() -> None:
    with pytest.raises(NetGuardError) as caught:
        check_scan_url(
            "https://nowhere.bank.internal/",
            registered_hosts=[*REGISTERED, "nowhere.bank.internal"],
            settings=_settings(),
            resolver=_resolve,
        )
    assert caught.value.code == "host_forbidden"


def test_chromium_resolves_only_registered_hosts() -> None:
    assert chromium_resolver_rules(["b", "a", "a"]) == "MAP * ~NOTFOUND, EXCLUDE a, EXCLUDE b"


@pytest.mark.parametrize(
    "address,forbidden",
    [
        ("169.254.169.254", True),
        ("fd00:ec2::254", True),
        ("100.100.100.200", True),
        ("192.0.0.192", True),
        ("::ffff:169.254.169.254", True),
        ("fe80::1", True),
        ("224.0.0.1", True),
        ("0.0.0.0", True),
        ("127.0.0.1", True),  # loopback, outside local
        ("10.0.0.5", False),  # RFC 1918 stays allowed (internal UAT sites, by design)
        ("172.18.0.5", False),
        ("192.168.1.10", False),
        ("8.8.8.8", False),
    ],
)
def test_forbidden_addresses(address: str, forbidden: bool) -> None:
    assert (forbidden_ip_reason(address, allow_loopback=False) is not None) is forbidden
    if address == "127.0.0.1":
        assert forbidden_ip_reason(address, allow_loopback=True) is None


@pytest.mark.parametrize("address", ["127.0.0.1", "127.8.9.10", "::1", "::ffff:127.0.0.1"])
def test_loopback_follows_the_environment_on_both_ip_versions(address: str) -> None:
    """``::1`` lies in the reserved ``::/8``: the loopback rule is decided first, so ``localhost``
    (127.0.0.1 and ::1) is a scan target in ``env=local`` and refused elsewhere."""
    assert forbidden_ip_reason(address, allow_loopback=False) == "loopback address"
    assert forbidden_ip_reason(address, allow_loopback=True) is None


def test_localhost_is_scannable_in_local_only() -> None:
    resolve = {"localhost": ["127.0.0.1", "::1"]}.__getitem__
    hosts = ["localhost"]
    url = "http://localhost:8410/apply"
    assert check_scan_url(url, registered_hosts=hosts, settings=_settings(), resolver=resolve)
    with pytest.raises(NetGuardError) as caught:
        check_scan_url(url, registered_hosts=hosts, settings=_settings(env="dev"), resolver=resolve)
    assert caught.value.code == "host_forbidden"
