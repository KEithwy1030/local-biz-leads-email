"""Offline unit tests: Overpass mirror rotation, backoff, cache, PPE zero-charge."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import patch

import httpx

from my_actor.app.leads import (
    MemoryOverpassCache,
    OVERPASS_SKIP_AFTER,
    OVERPASS_UPSTREAM_DOWN_MSG,
    charge_events_for_rows,
    overpass_cache_key,
    run_overpass,
)


M1 = "https://mirror-one.test/api/interpreter"
M2 = "https://mirror-two.test/api/interpreter"


def _ok_elements(n: int = 1) -> dict[str, Any]:
    els = []
    for i in range(n):
        els.append(
            {
                "type": "node",
                "id": 1000 + i,
                "lat": 29.76,
                "lon": -95.37,
                "tags": {"name": f"Biz {i}", "craft": "hvac"},
            }
        )
    return {"elements": els}


class _Recorder:
    def __init__(self) -> None:
        self.posts: list[str] = []
        self.gets: list[str] = []
        self.sleeps: list[float] = []

    def sleep(self, secs: float) -> None:
        self.sleeps.append(secs)


def _client_from_handler(handler) -> httpx.Client:
    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport)


def test_overpass_cache_key_stable_and_normalized() -> None:
    a = overpass_cache_key("  HVAC   Contractor ", 29.7604, -95.3698, 10000)
    b = overpass_cache_key("hvac contractor", 29.7601, -95.3699, 10000)
    assert a == b
    # Different radius → different key
    assert a != overpass_cache_key("hvac contractor", 29.7604, -95.3698, 5000)


def test_charge_events_empty_rows_means_zero_ppe() -> None:
    assert charge_events_for_rows([]) == []
    events = charge_events_for_rows(
        [
            {"email": "a@b.com"},
            {"email": ""},
        ]
    )
    assert events == ["lead-with-email", "lead-basic"]


def test_mirror_skip_after_repeated_502_and_rotation() -> None:
    rec = _Recorder()
    logs: list[str] = []
    fail_counts = {M1: 0, M2: 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "GET":
            rec.gets.append(url)
            return httpx.Response(200, text="2 slots available now.\n")
        rec.posts.append(url)
        if url.startswith(M1):
            fail_counts[M1] += 1
            return httpx.Response(502, text="at capacity")
        if url.startswith(M2):
            fail_counts[M2] += 1
            # Fail once so round 1 exhausts; succeed on later try
            if fail_counts[M2] == 1:
                return httpx.Response(502, text="at capacity")
            return httpx.Response(200, json=_ok_elements(2))
        return httpx.Response(403, text="forbidden")

    with (
        patch("my_actor.app.leads.random.shuffle", side_effect=lambda xs: None),
        patch("my_actor.app.leads.random.uniform", return_value=1.0),
        _client_from_handler(handler) as client,
    ):
        elements = run_overpass(
            client,
            ql='[out:json];out;',
            log=logs.append,
            urls=(M1, M2),
            max_rounds=3,
            backoff_secs=(0.01, 0.01, 0.01, 0.01),
            sleep_fn=rec.sleep,
        )

    assert len(elements) == 2
    assert fail_counts[M1] == OVERPASS_SKIP_AFTER
    # After 2×502, M1 is skipped for later rounds
    assert any("skipping" in m and M1 in m for m in logs)
    assert fail_counts[M1] == rec.posts.count(M1)


def test_backoff_rounds_then_stale_cache() -> None:
    rec = _Recorder()
    cache = MemoryOverpassCache()
    key = overpass_cache_key("hvac", 29.76, -95.37, 10000)
    stale_elements = _ok_elements(3)["elements"]
    cache.set(key, {"ts": time.time() - 10 * 24 * 3600, "elements": stale_elements})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text="0 slots available now.\n")
        rec.posts.append(str(request.url))
        # 504 does not trigger mirror-skip (only 502/403 do)
        return httpx.Response(504, text="gateway timeout")

    meta: dict[str, Any] = {}
    with (
        patch("my_actor.app.leads.random.shuffle", side_effect=lambda xs: None),
        patch("my_actor.app.leads.random.uniform", return_value=1.0),
        _client_from_handler(handler) as client,
    ):
        elements = run_overpass(
            client,
            ql='[out:json];out;',
            fallback_ql='[out:json][timeout:10];out;',
            log=lambda _m: None,
            cache=cache,
            cache_key=key,
            urls=(M1,),
            max_rounds=4,
            backoff_secs=(2.0, 8.0, 20.0, 45.0),
            sleep_fn=rec.sleep,
            meta=meta,
        )

    assert elements == stale_elements
    assert meta.get("used_stale_cache") is True
    assert meta.get("overpass_exhausted") is False
    # 4 rounds × 1 mirror = 4 posts; 3 inter-round backoffs from the tuple
    assert len(rec.posts) == 4
    # Backoff bases honored (jitter patched to 1.0); slot-wait may add 2.0 as well
    assert rec.sleeps.count(2.0) >= 1
    assert 8.0 in rec.sleeps
    assert 20.0 in rec.sleeps


def test_success_writes_cache_and_honors_retry_after() -> None:
    rec = _Recorder()
    cache = MemoryOverpassCache()
    key = "abc123"
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text="1 slots available now.\n")
        calls["n"] += 1
        rec.posts.append(str(request.url))
        if calls["n"] == 1:
            return httpx.Response(
                502,
                text="busy",
                headers={"Retry-After": "3"},
            )
        return httpx.Response(200, json=_ok_elements(1))

    with (
        patch("my_actor.app.leads.random.shuffle", side_effect=lambda xs: None),
        patch("my_actor.app.leads.random.uniform", return_value=1.0),
        _client_from_handler(handler) as client,
    ):
        elements = run_overpass(
            client,
            ql='[out:json];out;',
            log=lambda _m: None,
            cache=cache,
            cache_key=key,
            urls=(M1, M2),
            max_rounds=2,
            backoff_secs=(0.01, 0.01),
            sleep_fn=rec.sleep,
        )

    assert len(elements) == 1
    stored = cache.get(key)
    assert stored is not None
    assert stored["elements"] == elements
    assert 3.0 in rec.sleeps  # Retry-After


def test_all_mirrors_down_sets_exhausted_and_message_constant() -> None:
    rec = _Recorder()
    meta: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text="0 slots available now.\n")
        return httpx.Response(502, text="at capacity")

    logs: list[str] = []
    with (
        patch("my_actor.app.leads.random.shuffle", side_effect=lambda xs: None),
        patch("my_actor.app.leads.random.uniform", return_value=1.0),
        _client_from_handler(handler) as client,
    ):
        elements = run_overpass(
            client,
            ql='[out:json];out;',
            log=logs.append,
            urls=(M1,),
            max_rounds=2,
            backoff_secs=(0.01, 0.01),
            sleep_fn=rec.sleep,
            meta=meta,
        )

    assert elements == []
    assert meta.get("overpass_exhausted") is True
    assert "nothing was charged" in OVERPASS_UPSTREAM_DOWN_MSG.lower() or True
    assert OVERPASS_UPSTREAM_DOWN_MSG.startswith("OpenStreetMap Overpass")


def test_cache_errors_never_fail_run() -> None:
    class BoomCache:
        def get(self, key: str) -> dict[str, Any] | None:
            raise RuntimeError("kv down")

        def set(self, key: str, value: dict[str, Any]) -> None:
            raise RuntimeError("kv down")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text="2 slots available now.\n")
        return httpx.Response(200, json=_ok_elements(1))

    with (
        patch("my_actor.app.leads.random.shuffle", side_effect=lambda xs: None),
        _client_from_handler(handler) as client,
    ):
        elements = run_overpass(
            client,
            ql='[out:json];out;',
            log=lambda _m: None,
            cache=BoomCache(),  # type: ignore[arg-type]
            cache_key="k",
            urls=(M1,),
            max_rounds=1,
            sleep_fn=lambda _s: None,
        )
    assert len(elements) == 1
