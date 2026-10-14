"""S3 review F-20: a synthesis seed is key material — it keys the D-A key pseudonyms (its holder
maps every pseudonymised id back to the real one) — so ``wrap_key`` wraps it like a DEK.

``/synthesize`` (JSON and the NDJSON header) used to ignore ``wrap_key`` and return the clear seed;
``POST /jobs/run`` wrapped only a DEK; ``POST /job-groups`` had no wrap option and silently ignored
unknown fields. Now the seed comes back as ``{alg, wrapped_seed, wrap, seed_fingerprint}`` that only
the caller's private key opens, ``/job-groups`` takes ``wrap_key`` (key and seed), and refuses
unknown fields."""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from dps.app import create_app
from dps.config import Settings
from dps.engine.synth.surrogates import SynthesisSeed, unwrap_synthesis_seed
from dps.engine.transform.crypto import unwrap_dek
from dps.secrets import seed_fingerprint
from tests.api.test_api_connections import JOBS
from tests.api.test_api_jobs import _run, seeded  # noqa: F401 - the fixture

_PRIVATE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_PEM = _PRIVATE.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()
WRAP = {
    "type": "rsa-oaep-256",
    "public_key_pem": _PRIVATE.public_key()
    .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    .decode(),
}
RECORD = {"name": "Ann Smith", "email": "ann.smith@example-mail.in", "city": "Pune"}


def _assert_wrapped(seed: dict[str, Any]) -> bytes:
    assert "seed" not in seed and seed["wrapped_seed"] and seed["wrap"]["type"] == "rsa-oaep-256"
    raw = unwrap_synthesis_seed(seed["wrapped_seed"], PRIVATE_PEM)
    assert len(raw) == 32
    assert seed["seed_fingerprint"] == seed_fingerprint(
        base64.urlsafe_b64encode(raw).decode().rstrip("=")
    )
    return raw


def test_l1_synthesize_wraps_the_seed_in_json_and_ndjson() -> None:
    client = TestClient(
        create_app(Settings(_env_file=None, env="local", auth_enabled=False, llm_provider="stub"))
    )
    options = {"wrap_key": WRAP, "consistency": {"scope": "key"}}
    plain = client.post("/api/v1/synthesize", json={"data": RECORD, "options": options})
    assert plain.status_code == 200, plain.text
    raw = _assert_wrapped(plain.json()["synthesis_seed"])
    # the unwrapped seed is the seed: presented in clear, it reproduces the same output
    again = client.post(
        "/api/v1/synthesize",
        json={
            "data": RECORD,
            "options": {
                "consistency": {
                    "scope": "key",
                    "synthesis_seed": SynthesisSeed(
                        seed=base64.urlsafe_b64encode(raw).decode().rstrip("=")
                    ).model_dump(mode="json"),
                }
            },
        },
    )
    assert again.json()["result"] == plain.json()["result"]
    stream = client.post(
        "/api/v1/synthesize",
        content=json.dumps({"options": options}) + "\n" + json.dumps(RECORD) + "\n",
        headers={"Content-Type": "application/x-ndjson", "Accept": "application/x-ndjson"},
    )
    header = json.loads(stream.text.splitlines()[0])
    _assert_wrapped(header["synthesis_seed"])
    bad = client.post(
        "/api/v1/synthesize",
        json={"data": RECORD, "options": {"wrap_key": {"public_key_pem": "nope"}}},
    )
    assert bad.status_code == 400 and "nope" not in bad.text


@pytest.mark.db
def test_jobs_and_groups_wrap_the_seed(seeded) -> None:  # noqa: ANN001, F811
    c = seeded["c"]
    group = c.post("/api/v1/job-groups", json={"name": "wrapped", "wrap_key": WRAP}, headers=JOBS)
    assert group.status_code == 201, group.text
    body = group.json()
    _assert_wrapped(body["synthesis_seed"])
    key = body["key_material"]
    assert key["dek"] is None and len(unwrap_dek(key["wrapped_dek"], PRIVATE_PEM)) == 32
    stray = c.post("/api/v1/job-groups", json={"name": "stray", "wrapkey": WRAP}, headers=JOBS)
    assert stray.status_code == 422  # a misspelt wrap request is never silently ignored
    table = {
        "write_mode": "table",
        "connection_id": seeded["uat"],
        "object": {"owner": "bank_uat", "name": "customers"},
    }
    spec = _run(seeded["src"], seeded["snap_src"], table, mode="synthesize")
    spec["consistency"] = {"wrap_key": WRAP}
    run = c.post("/api/v1/jobs/run", json=spec, headers=JOBS)
    assert run.status_code == 202, run.text
    _assert_wrapped(run.json()["synthesis_seed"])
