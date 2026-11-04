"""Local business leads via OpenStreetMap (Nominatim primary, Overpass fallback)
+ optional email enrich from websites.

Free public data — no paid APIs, proxies, or LinkedIn cookies.
"""

from __future__ import annotations

import hashlib
import random
import re
import time
from typing import Any, Callable, Protocol
from urllib.parse import urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

USER_AGENT = (
    "keiwy-local-biz-leads-email/0.1.11 "
    "(Apify Actor; research; contact: via Apify Store listing)"
)
ACTOR_BUILD = "0.1.11"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
    "https://overpass.nchc.org.tw/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.osm.jp/api/interpreter",
)
OVERPASS_CACHE_STORE = "local-biz-overpass-cache"
OVERPASS_CACHE_TTL_SECS = 7 * 24 * 3600
OVERPASS_MAX_ROUNDS = 4
OVERPASS_BACKOFF_SECS = (2.0, 8.0, 20.0, 45.0)
OVERPASS_SKIP_AFTER = 2  # consecutive 502/403 before skipping a mirror for the run
OVERPASS_OUTPUT_RESERVE_SECS = 45.0  # leave time to write dataset / finish run
OVERPASS_UPSTREAM_DOWN_MSG = (
    "OpenStreetMap Overpass is overloaded; nothing was charged, retry later"
)

NOMINATIM_QUERY_SYNONYMS: dict[str, str] = {
    "plumber": "plumbing",
    "electrician": "electrical",
    "dentist": "dental",
    "mechanic": "auto repair",
    "realtor": "real estate",
}

EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
)
SKIP_EMAIL_SUBSTR = (
    "example.com",
    "email.com",
    "domain.com",
    "sentry.io",
    "wixpress.com",
    "cloudflare",
    "schema.org",
    "githubusercontent",
    "noreply",
    "no-reply",
    "donotreply",
    ".png",
    ".jpg",
    ".gif",
    ".svg",
    ".webp",
)

QUERY_TAG_HINTS: dict[str, list[tuple[str, str]]] = {
    "plumber": [("craft", "plumber")],
    "dentist": [("amenity", "dentist")],
    "dental": [("amenity", "dentist")],
    "restaurant": [("amenity", "restaurant")],
    "cafe": [("amenity", "cafe")],
    "coffee": [("amenity", "cafe")],
    "lawyer": [("office", "lawyer")],
    "attorney": [("office", "lawyer")],
    "gym": [("leisure", "fitness_centre")],
    "fitness": [("leisure", "fitness_centre")],
    "hotel": [("tourism", "hotel")],
    "barber": [("shop", "hairdresser")],
    "hairdresser": [("shop", "hairdresser")],
    "salon": [("shop", "hairdresser"), ("shop", "beauty")],
    "pharmacy": [("amenity", "pharmacy")],
    "clinic": [("amenity", "clinic")],
    "doctor": [("amenity", "doctors")],
    "bakery": [("shop", "bakery")],
    "florist": [("shop", "florist")],
    "electrician": [("craft", "electrician")],
    "hvac": [("craft", "hvac")],
    "roofer": [("craft", "roofer")],
    "mechanic": [("shop", "car_repair")],
    "vet": [("amenity", "veterinary")],
    "veterinary": [("amenity", "veterinary")],
    "accountant": [("office", "accountant")],
    "real estate": [("office", "estate_agent")],
    "realtor": [("office", "estate_agent")],
}

LogFn = Callable[[str], None]


def _noop_log(msg: str) -> None:
    pass


class OverpassCache(Protocol):
    """Sync cache for Overpass element lists (Apify named KV or in-memory)."""

    def get(self, key: str) -> dict[str, Any] | None: ...

    def set(self, key: str, value: dict[str, Any]) -> None: ...


class MemoryOverpassCache:
    """In-memory Overpass cache for tests / local runs."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        return self._data.get(key)

    def set(self, key: str, value: dict[str, Any]) -> None:
        self._data[key] = value


def overpass_cache_key(
    query: str,
    lat: float,
    lon: float,
    radius_m: int,
) -> str:
    """Stable key: hash(normalized query, rounded lat/lon, radius)."""
    norm_q = " ".join((query or "").strip().lower().split())
    raw = f"{norm_q}|{round(lat, 3)}|{round(lon, 3)}|{int(radius_m)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def _cache_get_elements(
    cache: OverpassCache | None,
    key: str,
    *,
    allow_stale: bool,
    log: LogFn = _noop_log,
) -> list[dict[str, Any]] | None:
    if cache is None:
        return None
    try:
        entry = cache.get(key)
        if not entry or not isinstance(entry, dict):
            return None
        elements = entry.get("elements")
        if not isinstance(elements, list):
            return None
        ts = float(entry.get("ts") or 0)
        age = time.time() - ts if ts else float("inf")
        fresh = age <= OVERPASS_CACHE_TTL_SECS
        if fresh or allow_stale:
            kind = "fresh" if fresh else "stale"
            log(f"Overpass cache hit ({kind}, age={int(age)}s) key={key[:12]}…")
            return elements
        log(f"Overpass cache entry expired (age={int(age)}s); ignoring")
        return None
    except Exception as exc:  # noqa: BLE001
        log(f"Overpass cache get skipped: {exc}")
        return None


def _cache_put_elements(
    cache: OverpassCache | None,
    key: str,
    elements: list[dict[str, Any]],
    log: LogFn = _noop_log,
) -> None:
    if cache is None:
        return
    try:
        cache.set(key, {"ts": time.time(), "elements": elements, "v": ACTOR_BUILD})
        log(f"Overpass cache wrote {len(elements)} elements key={key[:12]}…")
    except Exception as exc:  # noqa: BLE001
        log(f"Overpass cache set skipped: {exc}")


def _status_url(interpreter_url: str) -> str:
    """Derive /api/status (or …/status) from an interpreter URL."""
    if interpreter_url.rstrip("/").endswith("/interpreter"):
        return interpreter_url.rstrip("/").removesuffix("/interpreter") + "/status"
    parsed = urlparse(interpreter_url)
    path = parsed.path.rsplit("/", 1)[0] + "/status"
    return urlunparse(parsed._replace(path=path, query="", fragment=""))


def _parse_slots_available(status_text: str) -> int | None:
    m = re.search(r"(\d+)\s+slots?\s+available", status_text or "", re.I)
    if not m:
        return None
    return int(m.group(1))


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw.strip()))
    except ValueError:
        return None


def _backoff_with_jitter(base: float) -> float:
    # Full jitter in [0.5, 1.25] × base
    return max(0.0, base * random.uniform(0.5, 1.25))


def _deadline_exceeded(deadline: float | None, reserve: float = OVERPASS_OUTPUT_RESERVE_SECS) -> bool:
    if deadline is None:
        return False
    return time.monotonic() >= (deadline - reserve)


def _norm_website(raw: str | None) -> str:
    if not raw:
        return ""
    w = raw.strip()
    if not w:
        return ""
    if w.startswith("//"):
        w = "https:" + w
    if not re.match(r"^https?://", w, re.I):
        w = "https://" + w
    return w.rstrip("/")


def _tags_for_query(query: str) -> list[tuple[str, str]]:
    q = query.strip().lower()
    hits: list[tuple[str, str]] = []
    for key, pairs in QUERY_TAG_HINTS.items():
        if key in q:
            hits.extend(pairs)
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for p in hits:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _good_email(addr: str) -> bool:
    a = addr.strip().lower()
    if not a or "@" not in a:
        return False
    if any(s in a for s in SKIP_EMAIL_SUBSTR):
        return False
    if a.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".css", ".js")):
        return False
    return bool(EMAIL_RE.fullmatch(addr.strip()))


def extract_emails_from_html(html: str) -> list[str]:
    found: list[str] = []
    soup = BeautifulSoup(html, "lxml")
    for a in soup.select('a[href^="mailto:"]'):
        href = a.get("href") or ""
        raw = href.split(":", 1)[-1].split("?", 1)[0].strip()
        if _good_email(raw):
            found.append(raw.strip())
    for m in EMAIL_RE.finditer(soup.get_text(" ", strip=True)):
        if _good_email(m.group(0)):
            found.append(m.group(0))
    for m in re.finditer(r"mailto:([^\"'\\s>]+)", html, re.I):
        raw = m.group(1).split("?", 1)[0].strip()
        if _good_email(raw):
            found.append(raw)
    seen: set[str] = set()
    out: list[str] = []
    for e in found:
        key = e.lower()
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


CONTACT_PATHS = ("", "/contact", "/contact-us", "/contactus", "/about", "/about-us")


def enrich_email_from_website(
    client: httpx.Client,
    website: str,
    log: LogFn = _noop_log,
) -> tuple[str, str]:
    if not website:
        return "", ""
    base = website if website.startswith("http") else "https://" + website
    for path in CONTACT_PATHS:
        url = base if path == "" else base.rstrip("/") + path
        try:
            r = client.get(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
                timeout=15.0,
                follow_redirects=True,
            )
            if r.status_code >= 400:
                continue
            ctype = (r.headers.get("content-type") or "").lower()
            if "html" not in ctype and "text" not in ctype:
                continue
            emails = extract_emails_from_html(r.text)
            if emails:
                src = "mailto" if "mailto:" in r.text.lower() else "page_heuristic"
                log(f"Email from {url}: {emails[0]} ({src})")
                return emails[0], src
        except Exception as exc:  # noqa: BLE001
            log(f"enrich skip {url}: {exc}")
            continue
    return "", ""


def _address_from_nominatim(item: dict[str, Any]) -> str:
    addr = item.get("address") or {}
    parts = [
        " ".join(
            p
            for p in [
                addr.get("house_number", ""),
                addr.get("road", "") or addr.get("pedestrian", ""),
            ]
            if p
        ).strip(),
        addr.get("city")
        or addr.get("town")
        or addr.get("village")
        or addr.get("suburb")
        or "",
        addr.get("state", ""),
        addr.get("postcode", ""),
    ]
    line = ", ".join(p for p in parts if p)
    if line:
        return line
    # fallback: trim country from display_name
    dn = (item.get("display_name") or "").strip()
    return dn


def _name_from_nominatim(item: dict[str, Any]) -> str:
    dn = (item.get("display_name") or "").strip()
    if not dn:
        return ""
    # First comma-separated segment is usually the POI name
    return dn.split(",", 1)[0].strip()


def _parse_nominatim_item(item: dict[str, Any]) -> dict[str, str] | None:
    name = _name_from_nominatim(item)
    if not name:
        return None
    # Skip pure place/admin results when they look like the city itself
    cls = (item.get("class") or "").lower()
    typ = (item.get("type") or "").lower()
    if cls in {"boundary", "place"} and typ in {
        "city",
        "town",
        "state",
        "county",
        "country",
        "administrative",
    }:
        return None
    et = item.get("extratags") or {}
    website = _norm_website(
        et.get("website") or et.get("contact:website") or et.get("url") or ""
    )
    phone = (et.get("phone") or et.get("contact:phone") or "").strip()
    email = (et.get("email") or et.get("contact:email") or "").strip()
    osm_type = item.get("osm_type") or "node"
    osm_id = item.get("osm_id")
    source_url = (
        f"https://www.openstreetmap.org/{osm_type}/{osm_id}"
        if osm_id is not None
        else ""
    )
    email_good = bool(email and _good_email(email))
    return {
        "businessName": name,
        "website": website,
        "email": email if email_good else "",
        "phone": phone,
        "address": _address_from_nominatim(item),
        "sourceUrl": source_url,
        "emailSource": "osm_tag" if email_good else "",
    }


def _nominatim_search_single(
    client: httpx.Client,
    search_q: str,
    limit: int,
    log: LogFn = _noop_log,
) -> list[dict[str, str]]:
    log(f"Nominatim search q={search_q!r} limit={limit}")
    r = client.get(
        NOMINATIM_URL,
        params={
            "q": search_q,
            "format": "json",
            "limit": min(max(limit, 5), 50),
            "extratags": 1,
            "addressdetails": 1,
        },
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=45.0,
    )
    r.raise_for_status()
    data = r.json() or []
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in data:
        row = _parse_nominatim_item(item)
        if not row:
            continue
        key = row["businessName"].lower()
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    log(f"Nominatim returned {len(rows)} usable POIs for {search_q!r}")
    return rows


def nominatim_search_leads(
    client: httpx.Client,
    query: str,
    location: str,
    limit: int,
    log: LogFn = _noop_log,
) -> list[dict[str, str]]:
    """Primary path: Nominatim free-text search with extratags + backup queries on 0 rows."""
    q = f"{query} {location}".strip()
    rows = _nominatim_search_single(client, q, limit=limit, log=log)
    if rows:
        return rows

    # Nominatim 0 rows: try 1-2 fallback queries while respecting 1 req/s
    fallbacks: list[str] = []
    q_norm = query.strip().lower()
    alt_term = NOMINATIM_QUERY_SYNONYMS.get(q_norm)
    if not alt_term and q_norm.endswith("er") and len(q_norm) > 3:
        alt_term = f"{q_norm[:-2]}ing"
    if alt_term:
        fb_syn = f"{alt_term} {location}".strip()
        if fb_syn != q and fb_syn not in fallbacks:
            fallbacks.append(fb_syn)

    fb_near = f"{query.strip()} near {location}".strip()
    if fb_near != q and fb_near not in fallbacks:
        fallbacks.append(fb_near)

    for fb_q in fallbacks[:2]:
        time.sleep(1.1)  # Nominatim 1 req/s policy
        log(f"Nominatim 0 usable POIs for {q!r}; trying fallback query {fb_q!r}")
        try:
            fb_rows = _nominatim_search_single(client, fb_q, limit=limit, log=log)
            if fb_rows:
                return fb_rows
        except Exception as exc:  # noqa: BLE001
            log(f"Nominatim fallback {fb_q!r} failed: {exc}")

    return []


def geocode_location(
    client: httpx.Client,
    location: str,
    log: LogFn = _noop_log,
) -> tuple[float, float]:
    r = client.get(
        NOMINATIM_URL,
        params={"q": location, "format": "json", "limit": 1},
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=30.0,
    )
    r.raise_for_status()
    data = r.json()
    if not data:
        raise ValueError(f"Nominatim found no place for location={location!r}")
    lat = float(data[0]["lat"])
    lon = float(data[0]["lon"])
    log(f"Geocoded {location!r} → {lat},{lon}")
    return lat, lon


def build_overpass_query(
    query: str,
    lat: float,
    lon: float,
    radius_m: int = 10000,
    limit: int = 40,
    timeout: int = 25,
) -> str:
    """Smaller Overpass query to reduce timeouts."""
    tags = _tags_for_query(query)
    parts: list[str] = []
    for k, v in tags[:2]:
        parts.append(f'nwr["{k}"="{v}"](around:{radius_m},{lat},{lon});')
    if not tags:
        safe = re.escape(query.strip())
        if safe:
            parts.append(f'nwr["name"~"{safe}",i](around:{min(radius_m, 5000)},{lat},{lon});')
    if not parts:
        parts.append(f'nwr["amenity"](around:{min(radius_m, 3000)},{lat},{lon});')
    body = "\n  ".join(parts)
    return f"""[out:json][timeout:{timeout}];
(
  {body}
);
out center tags {max(limit, 15)};
"""


def _shrink_overpass_ql(ql: str) -> str:
    sec = re.sub(
        r"around:(\d+)",
        lambda m: f"around:{max(1000, int(m.group(1)) // 2)}",
        ql,
    )
    return re.sub(
        r"timeout:(\d+)",
        lambda m: f"timeout:{max(10, int(m.group(1)) // 2)}",
        sec,
    )


def run_overpass(
    client: httpx.Client,
    ql: str,
    fallback_ql: str | None = None,
    log: LogFn = _noop_log,
    *,
    cache: OverpassCache | None = None,
    cache_key: str | None = None,
    deadline_monotonic: float | None = None,
    urls: tuple[str, ...] | list[str] | None = None,
    max_rounds: int = OVERPASS_MAX_ROUNDS,
    backoff_secs: tuple[float, ...] = OVERPASS_BACKOFF_SECS,
    sleep_fn: Callable[[float], None] = time.sleep,
    meta: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Query Overpass with mirror rotation, multi-round backoff, and optional cache.

    On total upstream failure, returns a stale cache entry when available (else []).
    Never raises for mirror/HTTP failures.
    """
    last_err: Exception | None = None
    mirror_hard_fails: dict[str, int] = {}
    skipped: set[str] = set()
    status_probed: set[str] = set()
    mirrors = list(urls if urls is not None else OVERPASS_URLS)
    random.shuffle(mirrors)
    log(f"Overpass mirror order (shuffled): {', '.join(mirrors)}")

    sec_ql = fallback_ql or _shrink_overpass_ql(ql)

    def _maybe_wait_for_slot(url: str, round_idx: int) -> None:
        # Cheap hint only: one probe per mirror per round, short timeout, tiny wait
        probe_key = f"{round_idx}:{url}"
        if probe_key in status_probed or _deadline_exceeded(deadline_monotonic):
            return
        status_probed.add(probe_key)
        try:
            status_url = _status_url(url)
            r = client.get(
                status_url,
                headers={"User-Agent": USER_AGENT},
                timeout=3.0,
            )
            if r.status_code >= 400:
                return
            slots = _parse_slots_available(r.text)
            if slots is None:
                return
            if slots <= 0:
                wait = min(_backoff_with_jitter(2.0), 3.0)
                log(f"Overpass status {status_url}: 0 slots; brief wait {wait:.1f}s")
                sleep_fn(wait)
        except Exception:  # noqa: BLE001
            return

    def _mark_hard_fail(url: str, code: int) -> None:
        if code not in {403, 502}:
            return
        mirror_hard_fails[url] = mirror_hard_fails.get(url, 0) + 1
        if mirror_hard_fails[url] >= OVERPASS_SKIP_AFTER:
            skipped.add(url)
            log(
                f"Overpass skipping {url} for rest of run "
                f"after {mirror_hard_fails[url]}× HTTP {code}"
            )

    def _execute(query_str: str, label: str, round_idx: int) -> list[dict[str, Any]] | None:
        nonlocal last_err
        for url in mirrors:
            if url in skipped:
                continue
            if _deadline_exceeded(deadline_monotonic):
                log("Overpass stopping early to respect Actor timeout / write output")
                return None
            _maybe_wait_for_slot(url, round_idx)
            try:
                log(f"Overpass ({label}) POST {url}…")
                r = client.post(
                    url,
                    data={"data": query_str},
                    headers={"User-Agent": USER_AGENT},
                    timeout=30.0,
                )
                if r.status_code >= 400:
                    last_err = RuntimeError(f"Overpass {url} HTTP {r.status_code}")
                    log(str(last_err))
                    _mark_hard_fail(url, r.status_code)
                    retry_after = _retry_after_seconds(r)
                    if retry_after is not None:
                        wait = min(retry_after, 30.0)
                        log(f"Honoring Retry-After={wait:.1f}s from {url}")
                        sleep_fn(wait)
                    else:
                        sleep_fn(1.0)
                    continue
                elements = (r.json() or {}).get("elements") or []
                log(f"Overpass returned {len(elements)} elements from {url}")
                return elements
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                log(f"Overpass {url} failed: {exc}")
                sleep_fn(1.0)
        return None

    for round_idx in range(max(1, int(max_rounds))):
        if _deadline_exceeded(deadline_monotonic):
            log("Overpass stopping early to respect Actor timeout / write output")
            break
        use_fallback = round_idx >= 1 and bool(sec_ql)
        query_str = sec_ql if use_fallback else ql
        label = (
            f"round {round_idx + 1} fallback"
            if use_fallback and query_str != ql
            else f"round {round_idx + 1}"
        )
        elements = _execute(query_str, label, round_idx)
        if elements is not None:
            if cache_key:
                _cache_put_elements(cache, cache_key, elements, log=log)
            if meta is not None:
                meta["overpass_exhausted"] = False
                meta["used_stale_cache"] = False
            return elements

        if round_idx + 1 >= max_rounds:
            break
        base = backoff_secs[min(round_idx, len(backoff_secs) - 1)]
        wait = _backoff_with_jitter(base)
        if deadline_monotonic is not None:
            remaining = deadline_monotonic - OVERPASS_OUTPUT_RESERVE_SECS - time.monotonic()
            if remaining <= 0:
                log("Overpass no time left for backoff; stopping")
                break
            wait = min(wait, remaining)
        log(
            f"Overpass {label} all mirrors failed ({last_err}); "
            f"backoff {wait:.1f}s before next round…"
        )
        sleep_fn(wait)

    # Live Overpass failed — serve stale cache if any
    if cache_key:
        stale = _cache_get_elements(cache, cache_key, allow_stale=True, log=log)
        if stale is not None:
            log("Serving stale Overpass cache after upstream failure")
            if meta is not None:
                meta["used_stale_cache"] = True
                meta["overpass_exhausted"] = False
            return stale

    log(f"Overpass all failed ({last_err}); continuing with Nominatim-only rows")
    if meta is not None:
        meta["overpass_exhausted"] = True
        meta["used_stale_cache"] = False
    return []


def _element_to_row(el: dict[str, Any]) -> dict[str, str] | None:
    tags = el.get("tags") or {}
    name = (tags.get("name") or tags.get("name:en") or "").strip()
    if not name:
        return None
    website = _norm_website(
        tags.get("website")
        or tags.get("contact:website")
        or tags.get("url")
        or ""
    )
    phone = (
        tags.get("phone") or tags.get("contact:phone") or tags.get("mobile") or ""
    ).strip()
    email = (tags.get("email") or tags.get("contact:email") or "").strip()
    addr_parts = [
        tags.get("addr:housenumber", ""),
        tags.get("addr:street", ""),
        tags.get("addr:city", "") or tags.get("addr:town", ""),
        tags.get("addr:state", ""),
        tags.get("addr:postcode", ""),
    ]
    address = ", ".join(p for p in addr_parts if p).strip(", ")
    if not address:
        address = (tags.get("addr:full") or "").strip()
    osm_type = el.get("type") or "node"
    osm_id = el.get("id")
    source_url = (
        f"https://www.openstreetmap.org/{osm_type}/{osm_id}"
        if osm_id is not None
        else ""
    )
    email_source = "osm_tag" if email and _good_email(email) else ""
    if email and not _good_email(email):
        email = ""
    return {
        "businessName": name,
        "website": website,
        "email": email,
        "phone": phone,
        "address": address,
        "sourceUrl": source_url,
        "emailSource": email_source,
    }


def _merge_rows(
    primary: list[dict[str, str]],
    extra: list[dict[str, str]],
) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for row in primary + extra:
        key = row["businessName"].lower()
        if key in seen:
            # upgrade empty website/email if duplicate has them
            for existing in out:
                if existing["businessName"].lower() == key:
                    if not existing.get("website") and row.get("website"):
                        existing["website"] = row["website"]
                    if not existing.get("email") and row.get("email"):
                        existing["email"] = row["email"]
                        existing["emailSource"] = row.get("emailSource") or ""
                    break
            continue
        seen.add(key)
        out.append(row)
    return out


def collect_leads(
    query: str,
    location: str,
    max_items: int = 50,
    enrich_email: bool = True,
    only_with_email: bool = False,
    log: LogFn = _noop_log,
    *,
    cache: OverpassCache | None = None,
    deadline_monotonic: float | None = None,
    meta: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    query = (query or "").strip()
    location = (location or "").strip()
    if not query or not location:
        raise ValueError("query and location are required")
    max_items = max(1, min(int(max_items or 50), 500))
    only_with_email = bool(only_with_email)
    # Over-fetch when filtering to email-only so maxItems still means up to N email leads
    fetch_limit = max_items
    if only_with_email:
        fetch_limit = min(max(max_items * 3, max_items + 10), 500)

    if meta is not None:
        meta.setdefault("overpass_attempted", False)
        meta.setdefault("overpass_exhausted", False)
        meta.setdefault("used_stale_cache", False)
        meta["build"] = ACTOR_BUILD

    with httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        rows = nominatim_search_leads(
            client, query, location, limit=max(fetch_limit, 10), log=log
        )
        time.sleep(1.1)  # Nominatim 1 req/s policy

        need_more = (len(rows) == 0) or (sum(1 for r in rows if r.get("website")) < min(fetch_limit, 3))
        if need_more:
            # Overpass fill — never fail the run if it errors
            try:
                if meta is not None:
                    meta["overpass_attempted"] = True
                lat, lon = geocode_location(client, location, log=log)
                time.sleep(1.1)
                radius_m = 10000
                ckey = overpass_cache_key(query, lat, lon, radius_m)
                # Prefer a fresh cache hit before hitting mirrors
                cached = _cache_get_elements(
                    cache, ckey, allow_stale=False, log=log
                )
                if cached is not None:
                    elements = cached
                else:
                    ql = build_overpass_query(
                        query,
                        lat,
                        lon,
                        radius_m=radius_m,
                        limit=max(fetch_limit * 2, 20),
                        timeout=25,
                    )
                    fallback_ql = build_overpass_query(
                        query,
                        lat,
                        lon,
                        radius_m=5000,
                        limit=max(fetch_limit * 2, 20),
                        timeout=15,
                    )
                    elements = run_overpass(
                        client,
                        ql,
                        fallback_ql=fallback_ql,
                        log=log,
                        cache=cache,
                        cache_key=ckey,
                        deadline_monotonic=deadline_monotonic,
                        meta=meta,
                    )
                over_rows = []
                for el in elements:
                    row = _element_to_row(el)
                    if row:
                        over_rows.append(row)
                rows = _merge_rows(rows, over_rows)
            except Exception as exc:  # noqa: BLE001
                log(f"Overpass/geocode skipped: {exc}")
                if meta is not None:
                    meta["overpass_exhausted"] = True
        else:
            log("Nominatim already has enough website rows; skip Overpass")

        with_web = [r for r in rows if r.get("website")]
        without = [r for r in rows if not r.get("website")]
        # Keep a larger pool before email filter when only_with_email
        ordered = (with_web + without)[:fetch_limit]

        if enrich_email:
            for row in ordered:
                if row.get("email") or not row.get("website"):
                    continue
                # Early stop once we have enough email leads
                if only_with_email and sum(1 for r in ordered if r.get("email")) >= max_items:
                    break
                email, src = enrich_email_from_website(client, row["website"], log=log)
                if email:
                    row["email"] = email
                    row["emailSource"] = src
                time.sleep(0.35)

        for row in ordered:
            row["email"] = row.get("email") or ""
            row["emailSource"] = row.get("emailSource") or ""
            row["website"] = row.get("website") or ""
            row["phone"] = row.get("phone") or ""
            row["address"] = row.get("address") or ""

        if only_with_email:
            before = len(ordered)
            ordered = [r for r in ordered if (r.get("email") or "").strip()][:max_items]
            log(
                f"onlyWithEmail filter: {before} → {len(ordered)} rows with email "
                f"(maxItems={max_items})"
            )
        else:
            ordered = ordered[:max_items]

        log(
            f"Collected {len(ordered)} leads "
            f"(with_website={sum(1 for r in ordered if r['website'])}, "
            f"with_email={sum(1 for r in ordered if r['email'])})"
        )
        if (
            meta is not None
            and meta.get("overpass_exhausted")
            and len(ordered) == 0
        ):
            log(OVERPASS_UPSTREAM_DOWN_MSG)
        return ordered


def event_name_for_row(row: dict[str, str]) -> str:
    return "lead-with-email" if row.get("email") else "lead-basic"


def charge_events_for_rows(rows: list[dict[str, str]]) -> list[str]:
    """PPE events to charge for pushed rows. Empty input → no events ($0)."""
    return [event_name_for_row(r) for r in rows]
