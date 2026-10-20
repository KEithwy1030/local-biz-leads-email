"""Local business leads via OpenStreetMap (Nominatim primary, Overpass fallback)
+ optional email enrich from websites.

Free public data — no paid APIs, proxies, or LinkedIn cookies.
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

USER_AGENT = (
    "keiwy-local-biz-leads-email/0.1 "
    "(Apify Actor; research; contact: via Apify Store listing)"
)
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
)

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


def nominatim_search_leads(
    client: httpx.Client,
    query: str,
    location: str,
    limit: int,
    log: LogFn = _noop_log,
) -> list[dict[str, str]]:
    """Primary path: Nominatim free-text search with extratags."""
    q = f"{query} {location}".strip()
    log(f"Nominatim search q={q!r} limit={limit}")
    r = client.get(
        NOMINATIM_URL,
        params={
            "q": q,
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
        name = _name_from_nominatim(item)
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
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
            continue
        et = item.get("extratags") or {}
        website = _norm_website(
            et.get("website") or et.get("contact:website") or et.get("url") or ""
        )
        phone = (et.get("phone") or et.get("contact:phone") or "").strip()
        email = (et.get("email") or et.get("contact:email") or "").strip()
        email_source = "osm_tag" if email else ""
        osm_type = item.get("osm_type") or "node"
        osm_id = item.get("osm_id")
        source_url = (
            f"https://www.openstreetmap.org/{osm_type}/{osm_id}"
            if osm_id is not None
            else (item.get("osm_id") and "") or ""
        )
        if osm_id is not None:
            source_url = f"https://www.openstreetmap.org/{osm_type}/{osm_id}"

        seen.add(key)
        rows.append(
            {
                "businessName": name,
                "website": website,
                "email": email if _good_email(email) else (email if email else ""),
                "phone": phone,
                "address": _address_from_nominatim(item),
                "sourceUrl": source_url,
                "emailSource": email_source if (email and _good_email(email)) else "",
            }
        )
        if email and not _good_email(email):
            rows[-1]["email"] = ""
            rows[-1]["emailSource"] = ""

    log(f"Nominatim returned {len(rows)} usable POIs")
    return rows


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
    return f"""[out:json][timeout:30];
(
  {body}
);
out center tags {max(limit, 15)};
"""


def run_overpass(
    client: httpx.Client,
    ql: str,
    log: LogFn = _noop_log,
) -> list[dict[str, Any]]:
    last_err: Exception | None = None
    for url in OVERPASS_URLS:
        try:
            log(f"Overpass POST {url}…")
            r = client.post(
                url,
                data={"data": ql},
                headers={"User-Agent": USER_AGENT},
                timeout=45.0,
            )
            if r.status_code >= 400:
                last_err = RuntimeError(f"Overpass {url} HTTP {r.status_code}")
                log(str(last_err))
                continue
            elements = (r.json() or {}).get("elements") or []
            log(f"Overpass returned {len(elements)} elements from {url}")
            return elements
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            log(f"Overpass {url} failed: {exc}")
            time.sleep(0.5)
    log(f"Overpass all failed ({last_err}); continuing with Nominatim-only rows")
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

    with httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        rows = nominatim_search_leads(
            client, query, location, limit=max(fetch_limit, 10), log=log
        )
        time.sleep(1.1)  # Nominatim 1 req/s policy

        need_more = sum(1 for r in rows if r.get("website")) < min(fetch_limit, 3)
        if need_more:
            # Optional Overpass fill — never fail the run if it errors
            try:
                lat, lon = geocode_location(client, location, log=log)
                time.sleep(1.1)
                ql = build_overpass_query(
                    query, lat, lon, radius_m=10000, limit=max(fetch_limit * 2, 20)
                )
                elements = run_overpass(client, ql, log=log)
                over_rows = []
                for el in elements:
                    row = _element_to_row(el)
                    if row:
                        over_rows.append(row)
                rows = _merge_rows(rows, over_rows)
            except Exception as exc:  # noqa: BLE001
                log(f"Overpass/geocode skipped: {exc}")
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
        return ordered


def event_name_for_row(row: dict[str, str]) -> str:
    return "lead-with-email" if row.get("email") else "lead-basic"
