from Scraper.scrape_ufc_stats import scrape_fight_page


import asyncio
from playwright.async_api import async_playwright



Test_urls = [
    "http://ufcstats.com/fight-details/127ba4a1ccb3d4a6",
    "http://ufcstats.com/fight-details/30cdb851f6c63444",
    "http://ufcstats.com/fight-details/5727d5be8c373346",
    "http://ufcstats.com/fight-details/60de0423ae6ed097",
    "http://ufcstats.com/fight-details/7208e40818401e88",
    "http://ufcstats.com/fight-details/3bd159c1bed14700",
    "http://ufcstats.com/fight-details/410e6b9e01660b35",
    "http://ufcstats.com/fight-details/46d800ce45fdd3e6"
]

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()

        for url in Test_urls:
            fight_data = await scrape_fight_page(context, url)
            rounds = fight_data["round_stats"]

            print(f"\n{url}")
            print(f"  round count: {len(rounds)}")
            for r in rounds:
                print(f"    round {r['round']}: kd={r['fighter1'].get('kd')!r}, "
                      f"sig_str={r['fighter1'].get('sig_str')!r}, "
                      f"ctrl={r['fighter1'].get('ctrl')!r}")

        await browser.close()




if __name__ == "__main__":
    asyncio.run(main())