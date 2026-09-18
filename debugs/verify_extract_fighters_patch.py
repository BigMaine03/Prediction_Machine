"""
Throwaway verification script -- NOT part of the pipeline.
Confirms the extract_fighters patch produces two DISTINCT fighters
(different names, different URLs) for a small sample of fight pages,
before committing to a full re-scrape.

Run standalone: python verify_extract_fighters_patch.py
"""
import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

from Scraper.scrape_ufc_stats import extract_fighters, extract_general_info

# Mix of URLs: reuse a couple from the earlier round_stats debugging plus
# any others you want to spot-check. Add more freely.
TEST_URLS = [
    "http://ufcstats.com/fight-details/127ba4a1ccb3d4a6",
    "http://ufcstats.com/fight-details/30cdb851f6c63444",
    "http://ufcstats.com/fight-details/5727d5be8c373346",
    "http://ufcstats.com/fight-details/60de0423ae6ed097",
    "http://ufcstats.com/fight-details/7208e40818401e88",
    "http://ufcstats.com/fight-details/3bd159c1bed14700",
    "http://ufcstats.com/fight-details/410e6b9e01660b35",
    "http://ufcstats.com/fight-details/46d800ce45fdd3e6",
]


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

            result = extract_fighters(soup)
            f1_name = result["fighter1"]["name"]
            f1_url = result["fighter1"]["url"]
            f2_name = result["fighter2"]["name"]
            f2_url = result["fighter2"]["url"]

            weight_class = extract_general_info(soup).get("weight_class")

            is_distinct = (
                f1_name and f2_name
                and f1_name != f2_name
                and f1_url and f2_url
                and f1_url != f2_url
            )

            status = "PASS" if is_distinct else "FAIL"
            if is_distinct:
                pass_count += 1
            else:
                fail_count += 1

            print(f"[{status}] {url}")
            print(f"  weight class: {weight_class}")
            print(f"  fighter1: {f1_name!r}  ({f1_url})")
            print(f"  fighter2: {f2_name!r}  ({f2_url})")
            print()

        print("=" * 50)
        print(f"Passed: {pass_count} / {len(TEST_URLS)}")
        print(f"Failed: {fail_count} / {len(TEST_URLS)}")
        if fail_count:
            print("Investigate FAIL cases before running a full re-scrape.")
        else:
            print("All sample fights show two distinct fighters. Safe to proceed to full re-scrape.")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())