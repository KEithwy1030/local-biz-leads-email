# Local Business Leads (with Email when found)

Keyword + city → local business leads. Data comes from **OpenStreetMap** (Nominatim geocode + Overpass). Optional pass visits each business website / contact page for `mailto:` or obvious emails.

**Store (after push):** https://apify.com/keiwy/local-biz-leads-email

## Input

| Field | Required | Default | Notes |
|-------|----------|---------|-------|
| `query` | yes | — | e.g. `plumber`, `dentist`, `cafe` |
| `location` | yes | — | e.g. `Austin, TX` / `Berlin, Germany` |
| `maxItems` | no | `50` | 1–500 |
| `enrichEmail` | no | `true` | Crawl site for email |

## Output (dataset row)

`businessName`, `website`, `email`, `phone`, `address`, `sourceUrl`, `emailSource`

- `email` may be empty (`""`) if none found — row is still a valid **basic** lead.
- `emailSource`: `osm_tag` | `mailto` | `page_heuristic` | empty

## Suggested PPE (set on Store; not required for first build)

| Event | When | Suggested price |
|-------|------|-----------------|
| `lead-with-email` | row has email | **~$0.0015 / lead** (≈$1.5/1k) |
| `lead-basic` | no email | **~$0.0005 / lead** (optional; first version may charge email-only) |

Failed / empty runs should not charge.

## Try (example)

```json
{
  "query": "dentist",
  "location": "Austin, TX",
  "maxItems": 10,
  "enrichEmail": true
}
```

## Limits / honesty

- Coverage depends on OSM tagging in that city (dense cities work better).
- No LinkedIn, no paid enrichment APIs, no purchased proxies in this Actor.
- Be polite: small concurrency; Nominatim / Overpass public endpoints.

## Who it’s for

Agencies / freelancers who need a cheap local-biz list with websites (and emails when public) without buying a full B2B database seat.
