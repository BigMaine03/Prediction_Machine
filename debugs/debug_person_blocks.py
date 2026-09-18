"""
Debug script — dumps full b-fight-details__person blocks (name + W/L together).
Run standalone: python debug_person_blocks.py
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

        # find the status <i> tags directly, then walk up to their
        # containing person block
        status_tags = soup.find_all("i", class_="b-fight-details__person-status")
        print(f"Found {len(status_tags)} status tags\n")

        for i, status_tag in enumerate(status_tags):
            person_block = status_tag.find_parent(class_="b-fight-details__person")
            print(f"=== person block {i} (status={status_tag.get_text(strip=True)!r}) ===")
            if person_block:
                print(person_block.prettify())
            else:
                print("  (no b-fight-details__person ancestor found -- printing status tag's direct parent instead)")
                print(status_tag.parent.prettify())
            print()

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())