"""The smoke test pages connections without an empty ``cursor`` parameter (M3 G6 review N3).

``params={"cursor": None}`` makes httpx send ``cursor=`` (an empty string) on the first page; the
API happened to accept it. The first page now carries no cursor at all and later pages carry the
``next_cursor`` they were given.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tools import smoke_test


def _server(names: list[str]) -> tuple[httpx.Client, list[httpx.Request]]:
    seen: list[httpx.Request] = []
    half = len(names) // 2

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST":
            return httpx.Response(201, json={"id": "conn_new"})
        page: dict[str, Any]
        if request.url.params.get("cursor") == "c2":
            page = {
                "items": [{"name": n, "id": f"id_{n}"} for n in names[half:]],
                "next_cursor": None,
            }
        else:
            page = {
                "items": [{"name": n, "id": f"id_{n}"} for n in names[:half]],
                "next_cursor": "c2",
            }
        return httpx.Response(200, json=page)

    return httpx.Client(transport=httpx.MockTransport(handle), base_url="http://smoke.test"), seen


@pytest.mark.parametrize(
    ("ensure", "names"),
    [
        (smoke_test.ensure_connections, list(smoke_test.CONNECTIONS)),
        (smoke_test.ensure_m3_connections, list(smoke_test.M3_CONNECTIONS)),
    ],
)
def test_listing_sends_no_empty_cursor(ensure: Any, names: list[str]) -> None:
    client, seen = _server(names)
    ids, created = ensure(client)
    gets = [r for r in seen if r.method == "GET"]
    assert len(gets) == 2
    assert "cursor" not in gets[0].url.params  # first page: no cursor parameter at all
    assert gets[1].url.params["cursor"] == "c2"
    assert created == 0 and set(ids) == set(names)
