# OpenStreetMap Local Business Email Leads — No Google Maps API · ~$1.50/1k

**Apify Store:** https://apify.com/keiwy/local-biz-leads-email

Turn a **keyword + city** into local business leads from **OpenStreetMap** — name, website, phone, address, and **email when publicly listed** (OSM tags or a light crawl of the business site / contact page).

| Blue-ocean pins | |
|-----------------|--|
| **Source** | **OpenStreetMap** (Nominatim + Overpass) |
| **API key** | **No Google Maps API key** required |
| **Price** | Pay-per-event **~$1.50 per 1,000 leads with email** (`lead-with-email` $0.0015); basic leads without email **~$0.50/1k** |

This is **not** another Google Maps email scraper. If you need Maps ratings / reviews / place IDs, use a Maps actor. If you want **keyless, cheaper OSM local email leads**, this Actor is built for that intent.

## Honest comparison (API key · source · price)

| | This Actor (`keiwy/local-biz-leads-email`) | Typical Maps + email scrapers | OSM peer (e.g. flash_scraper class, Store list price) |
|--|-------------------------------------------|-------------------------------|------------------------------------------------------|
| **Google Maps API key** | **Not needed** | Often required or Maps-sourced | Not needed |
| **Data source** | **OpenStreetMap** | Google Maps / Places | OpenStreetMap |
| **Email leads price** | **~$1.50 / 1k** with email | Often ~$1.50–$1.80 / 1k saved email (Maps path) | Often **from ~$3.50 / 1k** (peer Store list) |
| **Maps ratings / reviews** | No (OSM does not have them) | Yes | Varies |

Prices above are public Store list prices / PPE event rates as of 2026-10 — they change; always check the Store page. We do not claim to beat Maps coverage; we claim **no Maps key + OSM + lower OSM-peer price**.

## Measured demo (real platform run)

| Field | Value |
|-------|--------|
| **Run id** | `OYFwhkqmbossJAq25` (SUCCEEDED) |
| **Input** | `query=dentist`, `location=Austin, TX`, `maxItems=10`, `enrichEmail=true` |
| **Leads returned** | **10** |
| **With email** | **4 / 10 (40%)** |
| **With phone** | **5 / 10 (50%)** |
| **With website** | **6 / 10 (60%)** |
| **Email sources** | `osm_tag` ×3, `mailto` ×1 |
| **Duration** | ~7 s |
| **PPE charged (self-test)** | 4× `lead-with-email` + 6× `lead-basic` |

Earlier smoke without website enrichment (`PiQQcfDTVtYENJPxn`, same city/niche, `enrichEmail=false`, maxItems=5): **1 / 5 emails (20%)**, all from `osm_tag`. Enrichment usually raises fill-rate when businesses publish a public site email.

**Coverage varies by city and OSM tagging.** Dense, well-tagged cities work better. Empty or low fill-rate on a niche/city is possible — try a broader query or another city.

## Second measured demo — Denver plumber (build 0.1.10)

| Field | Value |
|-------|--------|
| **Build** | **0.1.10** |
| **Run id** | `XEyfqQaOKlMDGtc2h` (SUCCEEDED) |
| **Input** | `query=plumber`, `location=Denver, CO`, `maxItems=10`, `enrichEmail=true` |
| **Leads returned** | **9** |
| **With email** | **2 / 9** |
| **Public proof Gist** | https://gist.github.com/KEithwy1030/c27c0a4802e3d675b9fba5fe72f48da7 |

Nominatim free-text `plumber` alone can return 0 usable POIs in some cities; **0.1.10** adds plumbing-trade synonym fallback + Overpass multi-mirror retries so this niche recovers.

## Public proof links (no Maps API)

| Proof | Link |
|-------|------|
| Concept / OSM case | https://gist.github.com/KEithwy1030/0979d9a3386c31b87cba502621950a91 |
| Austin dentist fill-rate | https://gist.github.com/KEithwy1030/0672e6daafba6b7de5d6f22f6fbceb0c |
| Denver plumber 0.1.10 | https://gist.github.com/KEithwy1030/c27c0a4802e3d675b9fba5fe72f48da7 |
| **Apify Store** | https://apify.com/keiwy/local-biz-leads-email |

## Who it’s for

- Agencies / freelancers building **local outreach lists** without a Maps API bill
- Founders validating a city niche (dentists, plumbers, cafes, gyms…)
- Buyers searching for **OpenStreetMap / no Google Maps API / cheap local business email leads**

## Quick start (Apify Console)

1. Open [keiwy/local-biz-leads-email](https://apify.com/keiwy/local-biz-leads-email)
2. Paste input like:

```json
{
  "query": "plumber",
  "location": "Austin, TX",
  "maxItems": 25,
  "enrichEmail": true,
  "onlyWithEmail": false
}
```

3. Run → download dataset (CSV/JSON)

**Want only rows with email?** Set `"onlyWithEmail": true`. The Actor drops empty-email rows before output and **does not charge `lead-basic`** for them — you only pay `lead-with-email` for returned rows. Pair with `enrichEmail=true` for higher fill-rate. `maxItems` then means up to that many **email** leads (extra candidates are fetched under the hood).

## Input

| Field | Required | Default | Notes |
|-------|----------|---------|-------|
| `query` | yes | — | e.g. `plumber`, `dentist`, `cafe` |
| `location` | yes | — | e.g. `Austin, TX` / `Berlin, Germany` |
| `maxItems` | no | `50` | 1–500 (with `onlyWithEmail`, up to N email leads) |
| `enrichEmail` | no | `true` | Visit site for `mailto:` / obvious emails |
| `onlyWithEmail` | no | `false` | If true, only output non-empty email rows; no `lead-basic` charge for dropped rows |

## Output fields

`businessName`, `website`, `email`, `phone`, `address`, `sourceUrl`, `emailSource`

- `email` may be `""` if none found — still a valid **basic** lead (unless `onlyWithEmail=true`, in which case empty-email rows are omitted)
- `emailSource`: `osm_tag` | `mailto` | `page_heuristic` | empty

## Pricing (PPE on Store)

| Event | When | Price |
|-------|------|-------|
| `lead-with-email` | row has email | **$0.0015** (~$1.50/1k) |
| `lead-basic` | no email | **$0.0005** (~$0.50/1k) |

Failed / empty runs should not charge for leads. Self-tests on your own account still consume platform compute usage.

## How it works (honest)

1. Geocode `location` via **Nominatim**
2. Query **Overpass** for matching OSM POIs (public Overpass mirrors; occasional timeouts possible)
3. Optional: fetch website / contact pages for public emails

No LinkedIn, no paid enrichment APIs, no purchased proxies in this Actor. No MX “verified inbox” claim — emails are **publicly listed** strings only.

## Limitations

- OSM coverage ≠ Google Maps coverage
- Overpass public endpoints can time out; retry or lower `maxItems`
- Email fill-rate depends on public tags + site crawl; not guaranteed
- Not a replacement for Maps scrapers when you need ratings, hours from Maps, or place IDs

## Run via API

```bash
curl "https://api.apify.com/v2/acts/keiwy~local-biz-leads-email/runs?token=YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"dentist","location":"Austin, TX","maxItems":10,"enrichEmail":true,"onlyWithEmail":true}'
```

## License

MIT — use freely; please keep Store link if you fork for commercial lists.
