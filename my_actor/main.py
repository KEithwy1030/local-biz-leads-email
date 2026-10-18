"""Apify Actor: query + location → local business leads (optional email)."""

from __future__ import annotations

from apify import Actor

from .app.leads import collect_leads, event_name_for_row


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
            f"onlyWithEmail={only_with_email}"
        )

        def log(msg: str) -> None:
            Actor.log.info(msg)

        rows = collect_leads(
            query=query.strip(),
            location=location.strip(),
            max_items=max_items,
            enrich_email=enrich_email,
            only_with_email=only_with_email,
            log=log,
        )

        if not rows:
            Actor.log.warning(
                "No leads found. Try a broader query/location or different city spelling."
            )

        charged_email = 0
        charged_basic = 0
        for row in rows:
            await Actor.push_data(row)
            event = event_name_for_row(row)
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
