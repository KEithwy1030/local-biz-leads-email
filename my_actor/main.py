"""Apify Actor: query + location → local business leads (optional email)."""

from __future__ import annotations

import asyncio
import concurrent.futures
import time
from typing import Any

from apify import Actor

from .app.leads import (
    ACTOR_BUILD,
    OVERPASS_CACHE_STORE,
    OVERPASS_UPSTREAM_DOWN_MSG,
    charge_events_for_rows,
    collect_leads,
)


def _run_coro_sync(coro: Any, timeout: float = 30.0) -> Any:
    """Run an async coroutine from sync code (even if an event loop is running)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    def _in_thread() -> Any:
        return asyncio.run(coro)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_in_thread).result(timeout=timeout)


class ApifyNamedKvCache:
    """Sync facade over an async Apify named key-value store."""

    def __init__(self, kvs: Any) -> None:
        self._kvs = kvs

    def get(self, key: str) -> dict[str, Any] | None:
        try:
            val = _run_coro_sync(self._kvs.get_value(key))
            return val if isinstance(val, dict) else None
        except Exception:  # noqa: BLE001
            return None

    def set(self, key: str, value: dict[str, Any]) -> None:
        _run_coro_sync(self._kvs.set_value(key, value))


async def _open_overpass_cache() -> ApifyNamedKvCache | None:
    try:
        kvs = await Actor.open_key_value_store(name=OVERPASS_CACHE_STORE)
        return ApifyNamedKvCache(kvs)
    except Exception as exc:  # noqa: BLE001
        Actor.log.warning(f"Overpass cache store unavailable: {exc}")
        return None


def _deadline_from_actor() -> float | None:
    """Monotonic deadline from Actor timeout_at when running on platform."""
    try:
        cfg = Actor.configuration
        timeout_at = getattr(cfg, "timeout_at", None)
        if timeout_at is None:
            return None
        remaining = timeout_at.timestamp() - time.time()
        if remaining <= 0:
            return time.monotonic()
        return time.monotonic() + remaining
    except Exception:  # noqa: BLE001
        return None


async def main() -> None:
    async with Actor:
        actor_input = await Actor.get_input() or {}
        query = actor_input.get("query")
        location = actor_input.get("location")
        max_items = actor_input.get("maxItems", 50)
        enrich_email = actor_input.get("enrichEmail", True)
        only_with_email = actor_input.get("onlyWithEmail", False)

        if not isinstance(query, str) or not query.strip():
            raise ValueError("Input field query is required (e.g. plumber, dentist).")
        if not isinstance(location, str) or not location.strip():
            raise ValueError(
                "Input field location is required (e.g. Austin, TX or Berlin, Germany)."
            )
        try:
            max_items = int(max_items)
        except (TypeError, ValueError):
            max_items = 50
        enrich_email = bool(enrich_email) if enrich_email is not None else True
        only_with_email = (
            bool(only_with_email) if only_with_email is not None else False
        )

        Actor.log.info(
            f"Collecting leads query={query!r} location={location!r} "
            f"maxItems={max_items} enrichEmail={enrich_email} "
            f"onlyWithEmail={only_with_email} build={ACTOR_BUILD}"
        )

        def log(msg: str) -> None:
            Actor.log.info(msg)

        cache = await _open_overpass_cache()
        meta: dict[str, Any] = {}
        rows = collect_leads(
            query=query.strip(),
            location=location.strip(),
            max_items=max_items,
            enrich_email=enrich_email,
            only_with_email=only_with_email,
            log=log,
            cache=cache,
            deadline_monotonic=_deadline_from_actor(),
            meta=meta,
        )

        if not rows:
            if meta.get("overpass_exhausted"):
                Actor.log.warning(OVERPASS_UPSTREAM_DOWN_MSG)
                try:
                    await Actor.set_status_message(
                        OVERPASS_UPSTREAM_DOWN_MSG,
                        is_terminal=True,
                    )
                except Exception as exc:  # noqa: BLE001
                    Actor.log.warning(f"set_status_message skipped: {exc}")
            else:
                Actor.log.warning(
                    "No leads found. Try a broader query/location or different city spelling."
                )

        # PPE: only charge for rows actually pushed — empty dataset ⇒ $0 result events
        events = charge_events_for_rows(rows)
        charged_email = 0
        charged_basic = 0
        for row, event in zip(rows, events, strict=True):
            await Actor.push_data(row)
            try:
                await Actor.charge(event_name=event, count=1)
                if event == "lead-with-email":
                    charged_email += 1
                else:
                    charged_basic += 1
            except Exception as exc:  # noqa: BLE001
                Actor.log.warning(f"Actor.charge({event}) skipped: {exc}")

        Actor.log.info(
            f"Done: {len(rows)} leads pushed "
            f"(charged lead-with-email={charged_email}, lead-basic={charged_basic})"
        )
