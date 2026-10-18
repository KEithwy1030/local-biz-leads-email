#!/usr/bin/env python3
"""Local smoke without Apify platform: collect >=1 lead with website."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# package layout: my_actor.app

from my_actor.app.leads import collect_leads  # noqa: E402


def main() -> int:
    query = "dentist"
    location = "Austin, TX"
    print(f"SMOKE query={query!r} location={location!r} maxItems=5 enrichEmail=False")
    rows = collect_leads(
        query=query,
        location=location,
        max_items=5,
        enrich_email=False,  # faster smoke; website presence is the gate
        log=print,
    )
    out = ROOT / "storage" / "smoke_output.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out} ({len(rows)} rows)")
    with_web = [r for r in rows if r.get("website")]
    if not with_web:
        print("FAIL: need >=1 row with website", file=sys.stderr)
        # still print what we got for debugging
        print(json.dumps(rows[:3], indent=2, ensure_ascii=False))
        return 1
    print("PASS: sample row:")
    print(json.dumps(with_web[0], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
