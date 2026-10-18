# Local Business Leads — Emails, Phones & Websites (OSM)

**Apify Store:** https://apify.com/keiwy/local-biz-leads-email

Turn a **keyword + city** into a CSV of local businesses: name, website, phone, address, and **email when publicly listed** (OpenStreetMap tags or a light crawl of the business site / contact page).

No Google Maps API key. Pay-per-event pricing on Apify (~**$1.50 per 1,000 leads with email**; cheaper for basic leads without email).

## Who it’s for

- Agencies / freelancers building local outreach lists
- Founders validating a city niche (dentists, plumbers, cafes, gyms…)
- Anyone who wants cheap local leads without a full B2B database seat

## Quick start (Apify Console)

1. Open [keiwy/local-biz-leads-email](https://apify.com/keiwy/local-biz-leads-email)
2. Paste input like:

```json
{
  "query": "plumber",
  "location": "Austin, TX",
  "maxItems": 25,
  "enrichEmail": true
}
```

3. Run → download dataset (CSV/JSON)

## Input

| Field | Required | Default | Notes |
|-------|----------|---------|-------|
| `query` | yes | — | e.g. `plumber`, `dentist`, `cafe` |
| `location` | yes | — | e.g. `Austin, TX` / `Berlin, Germany` |
| `maxItems` | no | `50` | 1–500 |
| `enrichEmail` | no | `true` | Visit site for `mailto:` / obvious emails |

## Output fields

`businessName`, `website`, `email`, `phone`, `address`, `sourceUrl`, `emailSource`

- `email` may be `""` if none found — still a valid **basic** lead
- `emailSource`: `osm_tag` | `mailto` | `page_heuristic` | empty

## Pricing (PPE on Store)

| Event | When | Price |
|-------|------|-------|
| `lead-with-email` | row has email | **$0.0015** (~$1.5/1k) |
| `lead-basic` | no email | **$0.0005** (~$0.5/1k) |

Failed / empty runs should not charge for leads.

## How it works (honest)

1. Geocode `location` via **Nominatim**
2. Query **Overpass** for matching OSM POIs
3. Optional: fetch website / contact pages for public emails

Coverage depends on OSM tagging (dense cities work better). No LinkedIn, no paid enrichment APIs, no purchased proxies in this Actor.

## Run via API

```bash
curl "https://api.apify.com/v2/acts/keiwy~local-biz-leads-email/runs?token=YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"dentist","location":"Austin, TX","maxItems":10,"enrichEmail":true}'
```

## License

MIT — use freely; please keep Store link if you fork for commercial lists.
