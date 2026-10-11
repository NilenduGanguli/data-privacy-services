from __future__ import annotations

import asyncio
import base64
import datetime
import json
import ssl
from pathlib import Path

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from dps.config import Settings
from dps.llm.gateway import LlmCallError, LlmGateway, LlmResult, LlmUnavailable, get_gateway
from dps.observability import LLM_CALLS

B64 = lambda s: base64.b64encode(s.encode()).decode()  # noqa: E731


def _stellar_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "llm_provider": "stellar",
        "stellar_base_url": "https://stellar.test/v1",
        "coin_url": "https://coin.test/token",
        "coin_client_id": B64("cid"),
        "coin_client_secret": B64("sec"),
        "coin_scope": B64("abc"),
        "coin_token_ttl_seconds": 100,
        "llm_model": "gemini-2.5-flash",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


async def test_stub_provider_echoes_and_counts() -> None:
    gw = LlmGateway(Settings(_env_file=None, llm_provider="stub"))
    assert gw.available()
    result = await gw.complete(
        [{"role": "user", "content": "Rewrite {PERSON_1} please"}], purpose="rewrite"
    )
    assert "{PERSON_1}" in result.text and result.provider == "stub"


async def test_unconfigured_provider_is_unavailable() -> None:
    gw = LlmGateway(Settings(_env_file=None, llm_provider=""))
    assert not gw.available()
    with pytest.raises(LlmUnavailable):
        await gw.complete([{"role": "user", "content": "x"}])


async def test_stellar_mints_a_token_and_calls_chat_completions() -> None:
    calls: list[httpx.Request] = []
    mints = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal mints
        calls.append(request)
        if request.url.host == "coin.test":
            mints += 1
            form = dict(p.split("=") for p in request.content.decode().split("&"))
            assert form["client_id"] == "cid" and form["scope"] == "coinscopeabc"
            return httpx.Response(200, json={"access_token": f"tok{mints}"})
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["authorization"] == f"Bearer tok{mints}"
        body = json.loads(request.content)
        assert body["model"] == "gemini-2.5-flash"
        assert body["messages"][0]["content"] == "hello {X_1}"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "hi {X_1}"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            },
        )

    now = [1000.0]
    gw = LlmGateway(
        _stellar_settings(), transport=httpx.MockTransport(handler), clock=lambda: now[0]
    )
    r1 = await gw.complete([{"role": "user", "content": "hello {X_1}"}])
    r2 = await gw.complete([{"role": "user", "content": "hello {X_1}"}])
    assert r1.text == "hi {X_1}" and r1.usage == {"prompt": 3, "completion": 2}
    assert mints == 1 and r2.text
    now[0] += 101  # TTL elapsed on the monotonic clock → re-mint
    await gw.complete([{"role": "user", "content": "hello {X_1}"}])
    assert mints == 2


async def test_stellar_errors_carry_no_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "coin.test":
            return httpx.Response(200, json={"access_token": "t"})
        return httpx.Response(503)

    gw = LlmGateway(_stellar_settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(LlmCallError) as err:
        await gw.complete([{"role": "user", "content": "SECRET-CONTENT"}])
    assert "SECRET-CONTENT" not in str(err.value)


# --- added beyond the plan ---------------------------------------------------------------------


def _failed(provider: str) -> float:
    return LLM_CALLS.labels(provider, "rewrite", "error")._value.get()  # noqa: SLF001


async def test_errors_never_chain_an_exception_that_could_quote_a_url_or_secret() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "coin.test":
            return httpx.Response(401, text="bad client secret sec for cid")
        raise AssertionError("no call without a token")

    gw = LlmGateway(_stellar_settings(), transport=httpx.MockTransport(handler))
    before = _failed("stellar")
    with pytest.raises(LlmCallError) as err:
        await gw.complete([{"role": "user", "content": "x"}])
    message = str(err.value)
    assert "secret" not in message and "cid" not in message
    assert err.value.__cause__ is None and err.value.__suppress_context__
    assert _failed("stellar") == before + 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json at all"),
        httpx.Response(200, json=["a", "list"]),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": [{"message": {"content": None}}]}),
    ],
)
async def test_malformed_completions_are_call_errors(response: httpx.Response) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "coin.test":
            return httpx.Response(200, json={"access_token": "t"})
        return response

    gw = LlmGateway(_stellar_settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(LlmCallError):
        await gw.complete([{"role": "user", "content": "x"}])


@pytest.mark.parametrize(
    "mint",
    [
        httpx.Response(200, text="<html>proxy login</html>"),
        httpx.Response(200, json={"token_type": "bearer"}),
        httpx.Response(200, json=["t"]),
    ],
)
async def test_malformed_mints_are_call_errors(mint: httpx.Response) -> None:
    gw = LlmGateway(_stellar_settings(), transport=httpx.MockTransport(lambda r: mint))
    with pytest.raises(LlmCallError):
        await gw.complete([{"role": "user", "content": "x"}])


async def test_bad_base64_credentials_fail_without_echoing_them() -> None:
    gw = LlmGateway(_stellar_settings(coin_client_secret="not base64 s3cr3t!"))
    with pytest.raises(LlmCallError) as err:
        await gw.complete([{"role": "user", "content": "x"}])
    assert "s3cr3t" not in str(err.value) and err.value.__cause__ is None


async def test_usage_is_optional_and_tolerant() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "coin.test":
            return httpx.Response(200, json={"access_token": "t"})
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": None, "completion_tokens": "x"},
            },
        )

    gw = LlmGateway(_stellar_settings(), transport=httpx.MockTransport(handler))
    result = await gw.complete([{"role": "user", "content": "x"}])
    assert result == LlmResult(
        text="ok",
        provider="stellar",
        model="gemini-2.5-flash",
        usage={"prompt": 0, "completion": 0},
    )


async def test_concurrent_calls_mint_one_token() -> None:
    mints = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal mints
        if request.url.host == "coin.test":
            mints += 1
            await asyncio.sleep(0.01)
            return httpx.Response(200, json={"access_token": "t"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    gw = LlmGateway(_stellar_settings(), transport=httpx.MockTransport(handler))
    results = await asyncio.gather(
        *(gw.complete([{"role": "user", "content": "x"}]) for _ in range(5))
    )
    assert [r.text for r in results] == ["ok"] * 5 and mints == 1


async def test_api_calls_send_the_bearer_and_timeout_settings() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "coin.test":
            seen["mint_ct"] = request.headers["content-type"]
            return httpx.Response(200, json={"access_token": "t"})
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    gw = LlmGateway(_stellar_settings(), transport=httpx.MockTransport(handler))
    await gw.complete([{"role": "user", "content": "x"}], temperature=0.7, max_tokens=99)
    assert seen["mint_ct"] == "application/x-www-form-urlencoded"
    assert seen["body"] == {
        "model": "gemini-2.5-flash",
        "temperature": 0.7,
        "max_tokens": 99,
        "messages": [{"role": "user", "content": "x"}],
    }


def test_availability_per_provider() -> None:
    assert not LlmGateway(_stellar_settings(stellar_base_url="")).available()
    assert LlmGateway(_stellar_settings()).available()
    assert not LlmGateway(Settings(_env_file=None, llm_provider="vertex")).available()
    gw = get_gateway(Settings(_env_file=None, llm_provider="stub"))
    assert gw.provider == "stub" and gw.model == "gemini-2.5-flash"


# --- SEC-1: the COIN token mint verifies TLS unless explicitly told not to ---------------------


def _ca_file(tmp_path: Path, common_name: str) -> str:
    """A self-signed CA certificate (PEM) whose subject names ``common_name``."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    path = tmp_path / f"{common_name}.pem"
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return str(path)


def _trusted(verify: object) -> list[str]:
    """The CA common names an SSL context passed as ``verify`` trusts (verification required)."""
    assert isinstance(verify, ssl.SSLContext)
    assert verify.verify_mode == ssl.CERT_REQUIRED and verify.check_hostname
    return [
        value
        for cert in verify.get_ca_certs()
        for rdn in cert["subject"]
        for key, value in rdn
        if key == "commonName"
    ]


def _ok(request: httpx.Request) -> httpx.Response:
    if request.url.host == "coin.test":
        return httpx.Response(200, json={"access_token": "t"})
    return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})


@pytest.fixture
def clients(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, object]]:
    """Records ``(first request host, verify argument)`` for every client the gateway opens."""
    seen: list[tuple[str, object]] = []
    real = httpx.AsyncClient

    class _Recording(real):
        def __init__(self, *args: object, **kwargs: object) -> None:
            self._verify_arg = kwargs.get("verify", True)
            super().__init__(*args, **kwargs)

        async def post(self, url: object, *args: object, **kwargs: object) -> httpx.Response:
            seen.append((httpx.URL(str(url)).host, self._verify_arg))
            return await super().post(url, *args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _Recording)
    return seen


async def test_coin_mint_verifies_tls_with_the_default_trust_store(clients) -> None:  # noqa: ANN001
    gw = LlmGateway(_stellar_settings(), transport=httpx.MockTransport(_ok))
    await gw.complete([{"role": "user", "content": "x"}])
    assert clients == [("coin.test", True), ("stellar.test", True)]
    assert gw.coin_tls == "verified"


async def test_coin_mint_trusts_the_coin_ca_file_then_the_ssl_cert_file(
    clients,  # noqa: ANN001
    tmp_path: Path,
) -> None:
    coin_ca, org_ca = _ca_file(tmp_path, "coin-ca"), _ca_file(tmp_path, "org-ca")
    gw = LlmGateway(
        _stellar_settings(coin_ca_file=coin_ca, ssl_cert_file=org_ca),
        transport=httpx.MockTransport(_ok),
    )
    await gw.complete([{"role": "user", "content": "x"}])
    (mint_host, mint_verify), (api_host, api_verify) = clients
    assert mint_host == "coin.test" and _trusted(mint_verify) == ["coin-ca"]
    assert api_host == "stellar.test" and _trusted(api_verify) == ["org-ca"]

    clients.clear()
    gw = LlmGateway(_stellar_settings(ssl_cert_file=org_ca), transport=httpx.MockTransport(_ok))
    await gw.complete([{"role": "user", "content": "x"}])
    assert [_trusted(verify) for _, verify in clients] == [["org-ca"], ["org-ca"]]


async def test_coin_mint_skips_verification_only_with_the_explicit_opt_in(
    clients,  # noqa: ANN001
    tmp_path: Path,
) -> None:
    org_ca = _ca_file(tmp_path, "org-ca")
    gw = LlmGateway(
        _stellar_settings(coin_tls_insecure=True, ssl_cert_file=org_ca),
        transport=httpx.MockTransport(_ok),
    )
    await gw.complete([{"role": "user", "content": "x"}])
    (mint_host, mint_verify), (api_host, api_verify) = clients
    assert mint_host == "coin.test" and mint_verify is False
    # The opt-in covers the mint only: Stellar API calls still verify.
    assert api_host == "stellar.test" and _trusted(api_verify) == ["org-ca"]
    assert gw.coin_tls == "insecure"


async def test_an_unreadable_ca_file_is_a_call_error_before_any_request(
    clients,  # noqa: ANN001
    tmp_path: Path,
) -> None:
    gw = LlmGateway(
        _stellar_settings(coin_ca_file=str(tmp_path / "missing.pem")),
        transport=httpx.MockTransport(_ok),
    )
    with pytest.raises(LlmCallError) as err:
        await gw.complete([{"role": "user", "content": "x"}])
    assert clients == [] and err.value.__cause__ is None
    assert "missing.pem" not in str(err.value)
