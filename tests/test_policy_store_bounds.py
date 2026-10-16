"""Out-of-range version numbers on the policy-store routes are refused as 422, never a 500.

``GET /policies/{name}/versions/2147483648`` (or ``99999999999``) and ``diff?from=99999999999``
overflowed the database's integer column and answered 500 ``internal_error`` (M5 G4 review N1).
Version numbers are bounded like the diff's ``to`` (1-999999): beyond that the request is invalid,
inside it an unpublished version is still 404 ``version_not_found``.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.db
FULL_T1 = {"X-API-KEY": "dps_full", "X-DPS-Tenant": "t1"}
NAME = "gdpr-pseudonymise"


def test_out_of_range_versions_are_refused_not_a_500(api_client) -> None:  # noqa: ANN001
    c, _ = api_client
    created = c.post("/api/v1/policies", json={"name": NAME, "base_preset": NAME}, headers=FULL_T1)
    assert created.status_code == 201, created.text
    cases = [
        (f"/api/v1/policies/{NAME}/versions/2147483648", {}),
        (f"/api/v1/policies/{NAME}/versions/99999999999", {}),
        (f"/api/v1/policies/{NAME}/versions/1000000", {}),
        (f"/api/v1/policies/{NAME}/versions/0", {}),
        (f"/api/v1/policies/{NAME}/diff", {"from": 99999999999, "to": 1}),
        (f"/api/v1/policies/{NAME}/diff", {"from": 2147483648, "to": "draft"}),
    ]
    for path, params in cases:
        got = c.get(path, params=params, headers=FULL_T1)
        assert got.status_code == 422, (path, params, got.status_code)
        assert "99999999999" not in got.text and "2147483648" not in got.text  # never echoed
    unpublished = c.get(f"/api/v1/policies/{NAME}/versions/999999", headers=FULL_T1)
    assert (unpublished.status_code, unpublished.json()["code"]) == (404, "version_not_found")
    first = c.get(f"/api/v1/policies/{NAME}/versions/1", headers=FULL_T1)
    assert first.status_code == 200
