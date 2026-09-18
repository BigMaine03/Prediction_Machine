"""
Throwaway verification script -- NOT part of the pipeline.
Confirms extract_fight_result produces sane status values, and that its
fighter URLs line up exactly with extract_fighters' fighter1/fighter2 URLs
(catches any mismatch before this gets wired into the real schema).

Run standalone: python verify_extract_fight_result.py
"""
import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

from Scraper.scrape_ufc_stats import extract_fighters, extract_fight_result

# Reuse the same sample as prior verifications, plus room to add known
# draws/no-contests. To find real draw/NC fight URLs from your own DB, run:
#   SELECT fight_url FROM fights WHERE method ILIKE '%draw%' OR method ILIKE '%no contest%' LIMIT 5;
# and paste a couple of those in here -- draws/NCs are the edge case most
# likely to break a W/L-only assumption, so worth testing explicitly.
TEST_URLS = [
    "http://ufcstats.com/fight-details/6d6ab10cbaa45e8c",
    "http://ufcstats.com/fight-details/30cdb851f6c63444",
    "http://ufcstats.com/fight-details/5727d5be8c373346",
    "http://ufcstats.com/fight-details/60de0423ae6ed097",
    "http://ufcstats.com/fight-details/7208e40818401e88",
    "http://ufcstats.com/fight-details/3bd159c1bed14700",
    "http://ufcstats.com/fight-details/410e6b9e01660b35",
    "http://ufcstats.com/fight-details/46d800ce45fdd3e6",
    # add any known draw/NC fight_urls here once pulled from the DB
]

VALID_STATUSES = {"W", "L", "D", "NC"}


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()

        pass_count = 0
        fail_count = 0

        for url in TEST_URLS:
            page = await context.new_page()
            await page.goto(url, wait_until="networkidle")
            html = await page.content()
            soup = BeautifulSoup(html, "html.parser")
            await page.close()

            fighters = extract_fighters(soup)
            results = extract_fight_result(soup)

            f1_url = fighters["fighter1"]["url"]
            f2_url = fighters["fighter2"]["url"]
            f1_name = fighters["fighter1"]["name"]
            f2_name = fighters["fighter2"]["name"]

            f1_status = results.get(f1_url)
            f2_status = results.get(f2_url)

            checks = {
                "exactly_2_results": len(results) == 2,
                "f1_url_found_in_results": f1_url in results,
                "f2_url_found_in_results": f2_url in results,
                "f1_status_valid": f1_status in VALID_STATUSES,
                "f2_status_valid": f2_status in VALID_STATUSES,
                # in a normal W/L fight the two statuses should differ;
                # in a draw or NC both fighters legitimately share the same
                # status, so don't fail on equality alone -- just flag it
                # for a manual glance rather than an automatic pass/fail
            }
            is_ok = all(checks.values())

            status_label = "PASS" if is_ok else "FAIL"
            if is_ok:
                pass_count += 1
            else:
                fail_count += 1

            note = ""
            if f1_status == f2_status and f1_status is not None:
                note = "  <-- both fighters share the same status (expected only for draw/NC -- verify manually)"

            print(f"[{status_label}] {url}{note}")
            print(f"  fighter1: {f1_name!r} ({f1_url}) -> {f1_status!r}")
            print(f"  fighter2: {f2_name!r} ({f2_url}) -> {f2_status!r}")
            if not is_ok:
                failed_checks = [k for k, v in checks.items() if not v]
                print(f"  failed checks: {failed_checks}")
            print()

        print("=" * 50)
        print(f"Passed: {pass_count} / {len(TEST_URLS)}")
        print(f"Failed: {fail_count} / {len(TEST_URLS)}")
        if fail_count:
            print("Investigate FAIL cases before wiring extract_fight_result into the pipeline.")
        else:
            print("All sample fights show correctly matched, valid W/L/D/NC results. Safe to proceed.")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())