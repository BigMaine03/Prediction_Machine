"""
Debug script — inspects real page structure, does NOT insert or fix anything.
Run standalone: python debug_round_table_structure.py
"""
import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

TEST_URL = "http://ufcstats.com/fight-details/127ba4a1ccb3d4a6"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(TEST_URL, wait_until="networkidle")
        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")

        tables = soup.find_all("table", class_="b-fight-details__table js-fight-table")
        print(f"Found {len(tables)} tables with class 'b-fight-details__table js-fight-table'\n")

        for i, table in enumerate(tables):
            print(f"--- Table {i} ---")
            thead = table.find("thead")
            print("Has <thead>:", thead is not None)
            if thead:
                print("thead text:", thead.get_text(" ", strip=True))
            else:
                first_row = table.find("tr")
                print("first <tr> text:", first_row.get_text(" ", strip=True) if first_row else None)

            # show first data row's first cell (where a "Round 1" label might live)
            rows = table.find_all("tr", class_="b-fight-details__table-row")
            print("data row count:", len(rows))
            if rows:
                first_data_row = rows[0]
                cells = first_data_row.find_all("td")
                print("first row, first cell text:", cells[0].get_text(" ", strip=True) if cells else None)
                print("first row, first cell classes:", cells[0].get("class") if cells else None)
            print()

        # also check the sections, in case tables aren't where we think
        sections = soup.find_all("section", class_="b-fight-details__section js-fight-section")
        print(f"\nFound {len(sections)} sections with class 'b-fight-details__section js-fight-section'")
        for i, sec in enumerate(sections):
            heading = sec.find(["h2", "h3"])
            print(f"Section {i}: heading = {heading.get_text(strip=True) if heading else None}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())