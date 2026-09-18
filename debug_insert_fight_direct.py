"""
Debug script -- calls insert_fight directly on one real fight, bypassing
ingest_pipeline's per-event exception swallowing, so any error in
_resolve_outcome (or elsewhere) surfaces immediately instead of being
silently caught and skipped.
Run standalone: python debug_insert_fight_direct.py
"""
import asyncio
from playwright.async_api import async_playwright

from Scraper.scrape_ufc_stats import scrape_fight_page
from Database.db import get_connection
from Database.insert_fighters import insert_fighter
from Database.insert_fights import insert_fight, _resolve_outcome

TEST_URL = "http://ufcstats.com/fight-details/127ba4a1ccb3d4a6"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()

        fight_dict = await scrape_fight_page(context, TEST_URL)
        await browser.close()

    print("fight_result:", fight_dict.get("fight_result"))
    print("fighters:", fight_dict.get("fighters", {}).get("fighter1", {}).get("url"),
          fight_dict.get("fighters", {}).get("fighter2", {}).get("url"))

    connection = get_connection()
    fighters = fight_dict.get("fighters", {})
    fighter1_id = insert_fighter(connection, fighters.get("fighter1"))
    fighter2_id = insert_fighter(connection, fighters.get("fighter2"))

    print("\nCalling _resolve_outcome directly...")
    winner_id, outcome_type = _resolve_outcome(fight_dict, fighter1_id, fighter2_id)
    print("winner_id:", winner_id, " outcome_type:", outcome_type)

    print("\nCalling insert_fight (this hits the real DB)...")
    # NOTE: uses a fake event_id=1 just to exercise the insert path; adjust
    # if that FK doesn't exist in your events table.
    fight_id = insert_fight(connection, fight_dict, event_id=1,
                             fighter1_id=fighter1_id, fighter2_id=fighter2_id)
    print("fight_id:", fight_id)

    connection.close()


if __name__ == "__main__":
    asyncio.run(main())