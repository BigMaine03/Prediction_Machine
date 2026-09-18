"""
Debug script — dumps the raw internal HTML of one combined name cell.
Run standalone: python debug_name_cell_structure.py
"""
import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

from Scraper.scrape_ufc_stats import extract_fighters  # not used, just keeping import style consistent

TEST_URL = "http://ufcstats.com/fight-details/127ba4a1ccb3d4a6"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(TEST_URL, wait_until="networkidle")
        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")

        name_cells = soup.select("td.b-fight-details__table-col.l-page_align_left")
        cell = name_cells[0]

        print("--- raw inner HTML of cell 0 ---")
        print(cell.prettify())

        print("\n--- all <a> tags inside this cell ---")
        for a in cell.find_all("a"):
            print(f"  text={a.get_text(strip=True)!r}, href={a.get('href')!r}, classes={a.get('class')}")

        print("\n--- all <p> tags inside this cell ---")
        for p in cell.find_all("p"):
            print(f"  classes={p.get('class')}, text={p.get_text(strip=True)!r}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())