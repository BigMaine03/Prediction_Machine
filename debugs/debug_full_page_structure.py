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

        # 1. Dump any element whose class contains "person" -- on UFCStats
        # fight pages, the top header block showing each fighter's name,
        # photo, and W/L/D/NC status typically lives under a class like
        # "b-fight-details__person".
        print("--- Elements with 'person' in class name ---")
        person_blocks = soup.find_all(class_=lambda c: c and "person" in " ".join(c).lower())
        for i, el in enumerate(person_blocks):
            print(f"\n=== person block {i} ===")
            print(el.prettify())

        # 2. Search for text nodes that are EXACTLY W, L, D, or NC (not just
        # containing those letters -- avoids false matches on unrelated text)
        print("\n--- Text nodes exactly matching W / L / D / NC ---")
        status_values = {"w", "l", "d", "nc"}
        for t in soup.find_all(string=True):
            stripped = t.strip()
            if stripped.lower() in status_values and stripped != "":
                parent = t.parent
                print(f"  text={stripped!r}, tag={parent.name}, classes={parent.get('class')}, "
                      f"parent_of_parent_classes={parent.parent.get('class') if parent.parent else None}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())