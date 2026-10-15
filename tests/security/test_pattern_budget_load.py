"""S3 review round 2 (R2-B1, R2-B2): the policy-pattern CPU budget under load and at scale.

R2-B1: the round-1 matcher stopped a pattern with the ``regex`` module's ``timeout``, which counts
the CPU of the whole process, and released the GIL while matching, so a busy Python thread starved
it. Ordinary concurrent load made legitimate deny-list scans fail with 422
``custom_pattern_too_slow``. A legitimate call must never be refused because of what other threads
or other requests do; the stacked-repeat attack (F-02) must still be refused.

R2-B2: the round-1 allowance (1 s per call plus 1 µs per character per pattern, accrued over a whole
NDJSON stream) let one call burn about 250 times its legitimate work. A call is now capped
(``DPS_PATTERN_CPU_SECONDS``), an NDJSON item can spend only what it earned itself (plus the
stream's small base), and deny lists are matched as a trie (cost per character independent of the
number of terms).

Every text and name here is synthetic. The heavy checks run in a child process with a hard time
limit, so a regression fails the test instead of hanging the run.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dps.app import create_app
from dps.config import Settings
from dps.engine.call_scope import call_scope
from dps.engine.detect.custom import PolicyCustomDetector, RecognizerSpec
from dps.engine.detect.regex_guard import PatternTooSlow

_SYLLABLES = ["ka", "vo", "ren", "tal", "mi", "zor", "qui", "lon", "dar", "sev", "nu", "pex"]
_WORDS = (
    "the customer called about the loan account and asked for a statement of the "
    "transactions in the last quarter the branch said the office was closed on monday"
).split()
_ATTACK = r"\d+\d+\d+[x!]"  # stacked repeats: passes the static filter, cubic on a digit run


def _names(count: int, seed: int = 5) -> list[str]:
    """``count`` distinct synthetic person-like names (no real identity)."""
    rnd = random.Random(seed)
    out: set[str] = set()
    while len(out) < count:
        first = "".join(rnd.choice(_SYLLABLES) for _ in range(2)).capitalize()
        last = "".join(rnd.choice(_SYLLABLES) for _ in range(3)).capitalize()
        out.add(f"{first} {last} {rnd.randint(1, 99)}")
    return sorted(out)


def _text(size: int, seed: int = 9) -> str:
    rnd = random.Random(seed)
    out: list[str] = []
    n = 0
    while n < size:
        word = rnd.choice(_WORDS)
        out.append(word)
        n += len(word) + 1
    return " ".join(out)[:size]


@contextmanager
def _busy_python_thread() -> Iterator[None]:
    """One thread of this process that runs plain Python until the block ends (what the event
    loop and other requests' handlers do)."""
    stop = threading.Event()

    def spin() -> None:
        x = 0
        while not stop.is_set():
            x = (x * 31 + 7) % 1_000_003

    thread = threading.Thread(target=spin, daemon=True)
    thread.start()
    time.sleep(0.05)
    try:
        yield
    finally:
        stop.set()
        thread.join()


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = dict(
        _env_file=None,
        env="local",
        auth_enabled=False,
        llm_provider="stub",
        ner_enabled=False,
        rate_limit_enabled=False,
    )
    return Settings(**{**base, **kw})


def _deny_policy(terms: list[str]) -> dict[str, Any]:
    return {
        "preset": "gdpr-pseudonymise",
        "detect": {
            "custom_recognizers": [
                {"name": "vip", "supported_entity": "VIP_NAME", "deny_list": terms}
            ]
        },
    }


def _pattern_policy(*patterns: str) -> dict[str, Any]:
    return {
        "preset": "gdpr-pseudonymise",
        "detect": {
            "custom_recognizers": [
                {
                    "name": "emp",
                    "supported_entity": "EMPLOYEE_ID",
                    "patterns": [
                        {"name": f"p{i}", "regex": p, "score": 0.5} for i, p in enumerate(patterns)
                    ],
                }
            ]
        },
    }


def test_concurrent_legitimate_scans_are_never_refused_while_python_runs() -> None:
    """R2-B1 at the matcher (the reviewer's probes): eight calls scan 400 KB with a 1000-name deny
    list at once while one Python thread spins. Before the fix every call was refused (the matcher
    was starved and the timeout counted the other threads' CPU)."""
    spec = RecognizerSpec(name="vip", supported_entity="VIP_NAME", deny_list=_names(1000))
    text = _text(400_000)

    def one(_: int) -> str:
        detector = PolicyCustomDetector([spec], [])
        with call_scope():
            try:
                detector.scan(text, None)
            except PatternTooSlow:
                return "refused"
        return "ok"

    with _busy_python_thread(), ThreadPoolExecutor(8) as pool:
        outcomes = list(pool.map(one, range(8)))
    assert outcomes == ["ok"] * 8


def test_the_stacked_repeat_attack_is_still_refused_under_load() -> None:
    """The F-02 attack keeps failing fast while other threads are busy: it is stopped on its own
    CPU, not on the process's."""
    spec = RecognizerSpec(
        name="emp",
        supported_entity="EMPLOYEE_ID",
        patterns=[{"name": "p", "regex": _ATTACK, "score": 0.5}],
    )
    with _busy_python_thread(), call_scope():
        started = time.perf_counter()
        with pytest.raises(PatternTooSlow) as refused:
            PolicyCustomDetector([spec], []).scan("1" * 2000, None)
        took = time.perf_counter() - started
    assert refused.value.code == "custom_pattern_too_slow"
    assert took < 2.0, took


def test_legitimate_requests_answer_200_under_concurrent_load_through_the_app() -> None:
    """R2-B1 end to end: through the real ASGI app (every middleware, the call scope), six
    deny-list requests (1000 names, 200 KB each) run beside four ordinary 1 MB requests and one
    spinning Python thread. All answer 200 — before the fix most deny-list requests answered 422.
    The F-02 attack through the same app is still refused with 422 under that load."""
    victim = {"text": _text(200_000), "policy": _deny_policy(_names(1000))}
    ordinary = {"text": _text(1_000_000, seed=3)}  # the default preset: no custom pattern
    with TestClient(create_app(_settings())) as client, _busy_python_thread():
        with ThreadPoolExecutor(10) as pool:
            loads = [pool.submit(client.post, "/api/v1/detect", json=ordinary) for _ in range(4)]
            victims = [pool.submit(client.post, "/api/v1/detect", json=victim) for _ in range(6)]
            answers = [f.result(timeout=600) for f in victims + loads]
        assert [r.status_code for r in answers] == [200] * 10, [
            (r.status_code, r.json().get("code")) for r in answers
        ]
        started = time.perf_counter()
        attack = client.post(
            "/api/v1/detect", json={"text": "1" * 2000, "policy": _pattern_policy(_ATTACK)}
        )
        took = time.perf_counter() - started
    assert attack.status_code == 422 and attack.json()["code"] == "custom_pattern_too_slow"
    assert took < 3.0, took


def _isolated(script: str, *, limit: float) -> dict[str, Any]:
    """Run ``script`` in a child process killed after ``limit`` seconds; its last stdout line is a
    JSON object. A child that runs out of time is a pattern budget that did not bound the call."""
    env = {
        k: v for k, v in os.environ.items() if not k.startswith("DPS_") or k.startswith("DPS_TEST_")
    }
    try:
        done = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=limit,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"a policy-pattern call ran for more than {limit:.0f} s: no ceiling")
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(done.stdout.strip().splitlines()[-1])  # type: ignore[no-any-return]


_SCALE_SCRIPT = """
import json, time
from fastapi.testclient import TestClient
from dps.app import create_app
from dps.config import Settings
CEILING = 2.0
client = TestClient(create_app(Settings(_env_file=None, env="local", auth_enabled=False,
    llm_provider="stub", ner_enabled=False, rate_limit_enabled=False,
    pattern_cpu_seconds=CEILING)))
LINEAR = [r"\\b[A-Z]{2}\\d{6}\\b", r"\\b\\d{4}[- ]\\d{4}[- ]\\d{4}\\b",
          r"\\b[a-z]+@[a-z]+\\.[a-z]{2,}\\b", r"\\bEMP-\\d{5}\\b", r"\\b\\d{3}-\\d{2}-\\d{4}\\b"]
def policy(attack):
    recognizers, count = [], 0
    for r in range(50):
        pats = []
        for _ in range(20):
            rx = r"\\d+\\d+\\d+[x!]" if attack and count == 0 else LINEAR[count % 5]
            pats.append({"name": "p%d" % count, "regex": rx, "score": 0.5})
            count += 1
        recognizers.append({"name": "r%d" % r, "supported_entity": "CUSTOM_%d" % r,
                            "patterns": pats})
    return {"preset": "gdpr-pseudonymise", "detect": {"custom_recognizers": recognizers}}
out = {"ceiling": CEILING}
filler = "alpha beta 12345 gamma ZZ delta 9876 epsilon "
for label, text, attack in (("linear", (filler * 2000)[:50000], False),
                            ("attack", "1" * 50000, True)):
    started = time.perf_counter()
    r = client.post("/api/v1/detect", json={"text": text, "policy": policy(attack)})
    out[label] = [r.status_code, r.json().get("code"), time.perf_counter() - started]
print(json.dumps(out))
"""


def test_a_thousand_patterns_on_50k_characters_are_refused_within_the_ceiling() -> None:
    """R2-B2 at scale: a policy of 1000 patterns (50 recognizers x 20) on a 50 KB text answers 200
    when every pattern is linear; with one stacked pattern among them, the call is refused once it
    has spent the call ceiling (``DPS_PATTERN_CPU_SECONDS``, 2 s here) — before the fix it ran
    51 CPU-seconds first (and 1000 CPU-seconds on 1 MB)."""
    out = _isolated(_SCALE_SCRIPT, limit=120)
    ceiling = float(out["ceiling"])
    status, _code, _took = out["linear"]
    assert status == 200, out
    status, code, took = out["attack"]
    assert (status, code) == (422, "custom_pattern_too_slow"), out
    assert float(took) < ceiling + 8.0, out


_STREAM_SCRIPT = """
import json, time
from fastapi.testclient import TestClient
from dps.app import create_app
from dps.config import Settings
client = TestClient(create_app(Settings(_env_file=None, env="local", auth_enabled=False,
    llm_provider="stub", ner_enabled=False, rate_limit_enabled=False)))
LINEAR = [r"\\bEMP-\\d{5}\\b", r"\\b[A-Z]{2}\\d{6}\\b"]
def recognizer(k, slow):
    pats = [{"name": "p%d" % i, "regex": LINEAR[i % 2], "score": 0.5} for i in range(20)]
    if slow:
        pats[-1] = {"name": "slow", "regex": r"\\d+\\d+\\d+[x!]", "score": 0.5}
    return {"name": "r%d" % k, "supported_entity": "CUSTOM_%d" % k, "patterns": pats}
policy = {"preset": "gdpr-pseudonymise",
          "detect": {"custom_recognizers": [recognizer(k, k == 0) for k in range(3)]}}
filler = "alpha beta gamma delta epsilon zeta eta theta "
benign = [{"note": (filler * 60)[:2000]} for _ in range(120)]
def stream(items):
    body = "\\n".join([json.dumps({"policy": policy})] + [json.dumps(i) for i in items]) + "\\n"
    started = time.perf_counter()
    r = client.post("/api/v1/detect", content=body.encode(),
                    headers={"content-type": "application/x-ndjson"})
    last = [json.loads(x) for x in r.text.splitlines() if x.strip()][-1]
    return r.status_code, last, time.perf_counter() - started
benign_status, benign_last, benign_took = stream(benign)
status, last, took = stream(benign + [{"note": "1" * 2000}])
print(json.dumps({"status": status, "last": [last.get("type"), last.get("code"),
                  last.get("index")], "took": took, "benign_took": benign_took,
                  "benign": [benign_status, benign_last.get("type")]}))
"""


def test_an_ndjson_item_cannot_spend_what_earlier_items_earned() -> None:
    """R2-B2: 120 benign items of 2 KB under a 60-pattern policy each earn an allowance; the
    121st item runs the stacked pattern on a digit run. It is refused on its own allowance (its
    earnings plus the small base) — before the fix it could spend everything the stream had
    accrued first (about 16 CPU-seconds here)."""
    out = _isolated(_STREAM_SCRIPT, limit=120)
    assert out["benign"] == [200, "report"], out
    assert out["status"] == 200, out
    assert out["last"] == ["error", "custom_pattern_too_slow", 120], out
    assert float(out["took"]) - float(out["benign_took"]) < 1.5, out
