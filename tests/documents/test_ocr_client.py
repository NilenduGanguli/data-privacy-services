from __future__ import annotations

import httpx
import pytest

from dps.config import Settings
from dps.layers.documents.ocr import (
    AzureReadClient,
    NullOcrClient,
    OcrError,
    OcrPage,
    OcrWord,
    build_ocr_client,
    ocr_words_to_points,
    parse_read_result,
    refresh_ocr_readiness,
)
from dps.observability import READINESS

BASE = "http://ocr.test:5000"
RESULT = {
    "status": "succeeded",
    "analyzeResult": {
        "version": "3.2.0",
        "readResults": [
            {
                "page": 1,
                "angle": 0.0,
                "width": 1700,
                "height": 2200,
                "unit": "pixel",
                "lines": [
                    {
                        "text": "SSN 123-45-6789",
                        "boundingBox": [200, 300, 600, 300, 600, 340, 200, 340],
                        "words": [
                            {
                                "text": "SSN",
                                "boundingBox": [200, 300, 280, 300, 280, 340, 200, 340],
                                "confidence": 0.99,
                            },
                            {
                                "text": "123-45-6789",
                                "boundingBox": [300, 300, 600, 300, 600, 340, 300, 340],
                                "confidence": 0.97,
                            },
                        ],
                    },
                    {
                        "text": "Ananya",
                        "boundingBox": [200, 400, 380, 400, 380, 440, 200, 440],
                        "words": [
                            {
                                "text": "Ananya",
                                "boundingBox": [200, 400, 380, 400, 380, 440, 200, 440],
                                "confidence": 0.71,
                            }
                        ],
                    },
                ],
            }
        ],
    },
}


class Script:
    """A MockTransport handler that replays responses and records requests."""

    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0)


def accepted(location: str = f"{BASE}/vision/v3.2/read/analyzeResults/op1") -> httpx.Response:
    return httpx.Response(202, headers={"Operation-Location": location})


def client(
    script: Script,
    *,
    sleeps: list[float] | None = None,
    now: list[float] | None = None,
    timeout: float = 60.0,
    key: str = "k-SECRET",
    health: str = "/health",
) -> AzureReadClient:
    clock = now if now is not None else [0.0]

    async def sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)
        clock[0] += seconds

    return AzureReadClient(
        endpoint=BASE,
        key=key,
        timeout_s=timeout,
        poll_interval_s=0.5,
        health_path=health,
        transport=httpx.MockTransport(script),
        sleep=sleep,
        clock=lambda: clock[0],
    )


async def test_read_polls_until_succeeded() -> None:
    script = Script(
        accepted(),
        httpx.Response(200, json={"status": "running"}),
        httpx.Response(200, json=RESULT),
    )
    sleeps: list[float] = []
    page = await client(script, sleeps=sleeps).read(b"\x89PNG fake")
    assert [w.text for w in page.words] == ["SSN", "123-45-6789", "Ananya"]
    assert [w.line for w in page.words] == [0, 0, 1] and page.unit == "pixel"
    assert sleeps == [0.5, 0.5]
    assert all(r.headers["Ocp-Apim-Subscription-Key"] == "k-SECRET" for r in script.requests)
    assert script.requests[0].url.path == "/vision/v3.2/read/analyze"
    assert script.requests[0].headers["Content-Type"] == "application/octet-stream"


async def test_operation_location_must_stay_on_the_endpoint_host() -> None:
    script = Script(accepted("http://evil.example/op/1"))
    with pytest.raises(OcrError) as exc:
        await client(script).read(b"x")
    assert exc.value.code == "ocr_protocol" and len(script.requests) == 1


async def test_relative_operation_location_is_resolved() -> None:
    script = Script(
        accepted("/vision/v3.2/read/analyzeResults/op2"), httpx.Response(200, json=RESULT)
    )
    await client(script).read(b"x")
    assert str(script.requests[1].url) == f"{BASE}/vision/v3.2/read/analyzeResults/op2"


@pytest.mark.parametrize(
    "status,code", [(401, "ocr_auth"), (415, "ocr_rejected"), (500, "ocr_unavailable")]
)
async def test_status_errors_map_to_codes(status: int, code: str) -> None:
    with pytest.raises(OcrError) as exc:
        await client(Script(httpx.Response(status))).read(b"x")
    assert exc.value.code == code


async def test_failed_status_and_timeout() -> None:
    with pytest.raises(OcrError) as failed:
        await client(Script(accepted(), httpx.Response(200, json={"status": "failed"}))).read(b"x")
    assert failed.value.code == "ocr_failed"
    running = [httpx.Response(200, json={"status": "running"}) for _ in range(10)]
    with pytest.raises(OcrError) as slow:
        await client(Script(accepted(), *running), timeout=2.0).read(b"x")
    assert slow.value.code == "ocr_timeout"


async def test_throttling_honours_retry_after() -> None:
    script = Script(
        httpx.Response(429, headers={"Retry-After": "2"}),
        accepted(),
        httpx.Response(429, headers={"Retry-After": "3"}),
        httpx.Response(200, json=RESULT),
    )
    sleeps: list[float] = []
    await client(script, sleeps=sleeps).read(b"x")
    assert sleeps == [2.0, 0.5, 3.0]


async def test_errors_never_carry_key_or_body() -> None:
    script = Script(accepted(), httpx.Response(500, text="SECRET-BODY-4111 1111 1111 1111"))
    with pytest.raises(OcrError) as exc:
        await client(script, key="k-SECRET").read(b"x")
    assert "SECRET" not in str(exc.value) and "4111" not in str(exc.value)


async def test_null_client() -> None:
    null = NullOcrClient()
    assert not null.configured() and await null.probe() == (False, "unconfigured")
    with pytest.raises(OcrError) as exc:
        await null.read(b"x")
    assert exc.value.code == "ocr_unconfigured"


async def test_probe_reports_up_down_and_configured() -> None:
    assert await client(Script(httpx.Response(200))).probe() == (True, "up")
    assert await client(Script(httpx.Response(503))).probe() == (False, "down:503")
    assert await client(Script(), health="").probe() == (True, "configured")


def test_words_to_points() -> None:
    page = parse_read_result(RESULT)
    words = ocr_words_to_points(page, points_per_px=72 / 200)
    ssn = words[1]
    assert (round(ssn.bbox.x0, 2), round(ssn.bbox.y0, 2), round(ssn.bbox.x1, 2)) == (
        108.0,
        108.0,
        216.0,
    )
    assert ssn.source == "ocr" and ssn.line_hint == 0 and ssn.confidence == 0.97
    inch = OcrPage(
        width=8.5,
        height=11,
        unit="inch",
        angle=0,
        words=[OcrWord("x", (1, 1, 2, 1, 2, 1.5, 1, 1.5), None, 0)],
    )
    assert ocr_words_to_points(inch, points_per_px=0.1)[0].bbox.x1 == 144.0


def test_builder_picks_the_client() -> None:
    assert isinstance(build_ocr_client(Settings(_env_file=None)), NullOcrClient)
    on = build_ocr_client(
        Settings(
            _env_file=None, ocr_provider="azure", azure_vision_endpoint=BASE, azure_vision_key="k"
        )
    )
    assert isinstance(on, AzureReadClient) and on.configured()
    no_key = Settings(_env_file=None, ocr_provider="azure", azure_vision_endpoint=BASE)
    assert isinstance(build_ocr_client(no_key), NullOcrClient)


async def test_refresh_readiness() -> None:
    await refresh_ocr_readiness(NullOcrClient(), Settings(_env_file=None))
    assert READINESS.snapshot()["components"]["ocr"]["detail"] == "unconfigured"
    await refresh_ocr_readiness(
        client(Script(httpx.Response(200))), Settings(_env_file=None, ocr_provider="azure")
    )
    component = READINESS.snapshot()["components"]["ocr"]
    assert (
        component["ok"] is True
        and component["detail"] == "up"
        and component["engine"] == "azure-read-v3.2"
    )


async def test_non_finite_retry_after_falls_back_to_the_default() -> None:
    script = Script(
        httpx.Response(429, headers={"Retry-After": "nan"}),
        accepted(),
        httpx.Response(200, json=RESULT),
    )
    sleeps: list[float] = []
    await client(script, sleeps=sleeps).read(b"x")
    assert sleeps == [1.0, 0.5]


@pytest.mark.parametrize(
    "body",
    [
        ["SSN 123-45-6789"],
        {"status": "succeeded", "analyzeResult": {"readResults": {"page": 1}}},
        {"status": "succeeded", "analyzeResult": {"readResults": [{"width": "wide"}]}},
        {"status": "succeeded", "analyzeResult": {"readResults": [{"lines": "SSN 123-45-6789"}]}},
        {"status": "succeeded", "analyzeResult": {"readResults": [{"lines": [{"words": "x"}]}]}},
    ],
)
async def test_results_of_an_unexpected_shape_are_protocol_errors(body: object) -> None:
    with pytest.raises(OcrError) as exc:
        await client(Script(accepted(), httpx.Response(200, json=body))).read(b"x")
    assert exc.value.code == "ocr_protocol" and "123-45-6789" not in str(exc.value)


def _one_word(text: str, box: list[object]) -> dict[str, object]:
    line = {"words": [{"text": text, "boundingBox": box, "confidence": 0.9}]}
    return {"analyzeResult": {"readResults": [{"width": 10, "height": 10, "lines": [line]}]}}


@pytest.mark.parametrize(
    "box",
    [[1, 2, 3], [0, 0, "x", 0, 1, 1, 0, 1], [0, 0, float("nan"), 0, 1, 1, 0, 1], None],
)
def test_words_without_usable_geometry_fail_closed(box: list[object] | None) -> None:
    # A word that cannot be placed could never be boxed: refuse the page rather than drop it.
    with pytest.raises(OcrError) as exc:
        parse_read_result(_one_word("123-45-6789", box))  # type: ignore[arg-type]
    assert exc.value.code == "ocr_protocol" and "123-45-6789" not in str(exc.value)
    assert parse_read_result(_one_word("  ", box)).words == []  # type: ignore[arg-type]


def _page(**fields: object) -> dict[str, object]:
    page: dict[str, object] = {"width": 10, "height": 10, "unit": "pixel", "lines": []}
    page.update(fields)
    return {"status": "succeeded", "analyzeResult": {"readResults": [page]}}


def _line(text: object, words: object = None, **extra: object) -> dict[str, object]:
    line: dict[str, object] = {"text": text, **extra}
    if words is not None:
        line["words"] = words
    return line


def _w(text: object, box: object = (0, 0, 1, 0, 1, 1, 0, 1)) -> dict[str, object]:
    return {"text": text, "boundingBox": list(box) if isinstance(box, tuple) else box}


@pytest.mark.parametrize(
    "body",
    [
        {"status": "succeeded"},  # no analyzeResult at all
        {"status": "succeeded", "analyzeResult": "x"},
        {"status": "succeeded", "analyzeResult": {}},
        {"status": "succeeded", "analyzeResult": {"readResults": {}}},
        {"status": "succeeded", "analyzeResult": {"readResults": []}},
        {"status": "succeeded", "analyzeResult": {"readResults": [{}]}},  # no lines
        {"status": "succeeded", "analyzeResult": {"readResults": [{"lines": None}]}},
        {"status": "succeeded", "analyzeResult": {"readResults": ["SSN 123-45-6789"]}},
        # a v4-style body (another API version behind the gateway)
        {
            "status": "succeeded",
            "readResult": {"blocks": [{"lines": [{"text": "SSN 123-45-6789"}]}]},
        },
        # DPS sends one page image: a second page is not a result for it
        {"status": "succeeded", "analyzeResult": {"readResults": [{"lines": []}, {"lines": []}]}},
    ],
)
async def test_succeeded_results_without_exactly_one_page_fail_closed(body: object) -> None:
    # A shape the parser does not know must never read as "a page without words": that page's
    # values would go undetected and stay visible in a redacted raster while the report passed.
    with pytest.raises(OcrError) as exc:
        await client(Script(accepted(), httpx.Response(200, json=body))).read(b"x")
    assert exc.value.code == "ocr_protocol" and "123-45-6789" not in str(exc.value)


@pytest.mark.parametrize(
    "line",
    [
        _line("SSN 123-45-6789", []),
        _line("SSN 123-45-6789"),  # words missing
        _line("SSN 123-45-6789", [_w("  ")]),  # only blank words
        _line("SSN 123-45-6789", [_w("SSN")]),  # a word of the line is missing
        _line("SSN 123-45-6789", [_w("SSN"), _w("123-45-678")]),
    ],
)
def test_line_text_the_words_do_not_carry_fails_closed(line: dict[str, object]) -> None:
    with pytest.raises(OcrError) as exc:
        parse_read_result(_page(lines=[line]))
    assert exc.value.code == "ocr_protocol" and "123-45-6789" not in str(exc.value)


def test_lines_and_words_that_agree_are_read() -> None:
    page = parse_read_result(
        _page(
            lines=[
                _line("", []),  # a blank line without words is nothing to lose
                _line("  "),
                {"words": [_w("Ananya")]},  # no line text: the words are the text
                _line("SSN 123-45-6789", [_w("SSN"), _w(" 123-45-6789 ")]),
                _line("株式会社", [_w("株式"), _w("会社")]),  # scripts without spaces
            ]
        )
    )
    assert [w.text for w in page.words] == ["Ananya", "SSN", "123-45-6789", "株式", "会社"]
    assert parse_read_result(_page()).words == []  # the mock's blank page: lines == []


@pytest.mark.parametrize(
    "word",
    [
        _w({"v": "123-45-6789"}),
        _w(123456789),
        {"boundingBox": [0, 0, 1, 0, 1, 1, 0, 1]},  # no text
        "123-45-6789",
    ],
)
def test_word_text_must_be_a_string(word: object) -> None:
    with pytest.raises(OcrError) as exc:
        parse_read_result(_page(lines=[{"words": [word]}]))
    assert exc.value.code == "ocr_protocol" and "123-45-6789" not in str(exc.value)


@pytest.mark.parametrize(
    "fields",
    [
        {"unit": "cm"},
        {"unit": 1},
        {"width": "1700"},
        {"width": "nan"},
        {"height": float("inf")},
        {"angle": float("nan")},
        {"width": True},
        {"width": 10**400},  # an integer JSON accepts but no float holds
        {"lines": "SSN"},
        {"lines": ["SSN 123-45-6789"]},
        {"lines": [{"text": 12, "words": []}]},
        {"lines": [{"text": "x", "words": "x"}]},
    ],
)
def test_page_fields_of_the_wrong_type_fail_closed(fields: dict[str, object]) -> None:
    with pytest.raises(OcrError) as exc:
        parse_read_result(_page(**fields))
    assert exc.value.code == "ocr_protocol"


def test_numbers_beyond_a_float_never_escape_uncoded() -> None:
    with pytest.raises(OcrError) as box:
        parse_read_result(_page(lines=[{"words": [_w("a", [10**400] + [0] * 7)]}]))
    assert box.value.code == "ocr_protocol"
    word = {**_w("a"), "confidence": 10**400}
    assert parse_read_result(_page(lines=[{"words": [word]}])).words[0].confidence is None


@pytest.mark.parametrize(
    "location",
    [
        "http://ocr.test:99999/x",
        "http://ocr.test:abc/x",
        "http://[::1/x",
        "http://ocr.test:5000\t.evil.example/x",
        "http://ocr。test:5000/x",  # an IDN full-width dot
    ],
)
async def test_malformed_operation_locations_are_protocol_errors(location: str) -> None:
    # Raw header bytes, as a server sends them (httpx.Response(headers=str) refuses non-ASCII).
    script = Script(
        httpx.Response(202, headers=[(b"Operation-Location", location.encode("utf-8"))])
    )
    with pytest.raises(OcrError) as exc:
        await client(script).read(b"x")
    assert exc.value.code == "ocr_protocol" and len(script.requests) == 1


async def test_throttled_submits_do_not_sleep_after_the_last_attempt() -> None:
    script = Script(*[httpx.Response(429, headers={"Retry-After": "7"}) for _ in range(3)])
    sleeps: list[float] = []
    with pytest.raises(OcrError) as exc:
        await client(script, sleeps=sleeps).read(b"x")
    assert exc.value.code == "ocr_throttled" and len(script.requests) == 3
    assert sleeps == [7.0, 7.0]
