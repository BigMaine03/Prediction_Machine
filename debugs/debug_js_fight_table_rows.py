"""
Debug script — dumps row-by-row structure of the js-fight-table-classed tables.
Run standalone: python debug_js_fight_table_rows.py
"""
import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

TEST_URL = " http://ufcstats.com/fight-details/00e8d4b961a65d21"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(TEST_URL, wait_until="networkidle")
        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")

        js_tables = soup.find_all("table", class_="b-fight-details__table js-fight-table")

        for t_idx, table in enumerate(js_tables):
            print(f"\n=== js-fight-table {t_idx} ===")
            all_rows = table.find_all("tr")
            print(f"total <tr> count: {len(all_rows)}\n")
            for r_idx, row in enumerate(all_rows):
                cells = row.find_all(["th", "td"])
                cell_texts = [c.get_text(" ", strip=True) for c in cells]
                cell_tags = [c.name for c in cells]
                row_classes = row.get("class")
                print(f"row {r_idx} (classes={row_classes}):")
                print(f"  tags:  {cell_tags}")
                print(f"  texts: {cell_texts}")
                print()

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())