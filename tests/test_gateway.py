from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest

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
