import asyncio
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# Change this number to control how many tabs that can open at the same time.
MAX_CONCURRENT_TABS = 5


def _clean_text(value):
    if value is None:
        return None
    if hasattr(value, "get_text"):
        return value.get_text(" ", strip=True)
    return str(value).strip()


def _normalize_label(value):
    if value is None:
        return ""
    return " ".join(str(value).strip().lower().replace(":", "").split())


def _extract_label_value(soup, label_text):
    """Find a label and return the next sibling text content."""
    label_text_norm = _normalize_label(label_text)
    for label in soup.find_all(lambda tag: tag.name == "i"):
        if _normalize_label(label.get_text(" ", strip=True)) == label_text_norm:
            for sibling in label.next_siblings:
                if getattr(sibling, "name", None) is None:
                    raw_text = str(sibling).strip()
                    if raw_text:
                        return raw_text
                else:
                    text_value = _clean_text(sibling)
                    if text_value:
                        return text_value
            break
    return None


def extract_general_info(soup):
    """Extract general fight info such as weight class, method, round, and time."""
    # Weight class:
    # Located in <i class="b-fight-details__fight-title">
    # Method:
    # Located in <i class="b-fight-details__label">Method:</i>
    # Value is stored as the next sibling text node.
    # Round:
    # Located in <i class="b-fight-details__label">Round:</i>
    # Value is stored as the next sibling text node.
    # Time:
    # Located in <i class="b-fight-details__label">Time:</i>
    # Value is stored as the next sibling text node.
    # Time format:
    # Located in <i class="b-fight-details__label">Time format:</i>
    # Value is stored as the next sibling text node.
    # Referee:
    # Located in <i class="b-fight-details__label">Referee:</i>
    # Value is stored as the next sibling text node.
    return {
        "weight_class": _extract_label_value(soup, "Weight class"),
        "method": _extract_label_value(soup, "Method"),
        "round": _extract_label_value(soup, "Round"),
        "time": _extract_label_value(soup, "Time"),
        "time_format": _extract_label_value(soup, "Time format"),
        "referee": _extract_label_value(soup, "Referee"),
        "details": [],
    }


def extract_fighters(soup):
    """Extract fighter-level information for both sides of the fight."""
    # Fighter 1 and fighter 2 blocks are located in the fight card markup.
    # Each should expose: name, url, result, kd, sig_str.
    return {
        "fighter1": {
            "name": None,
            "url": None,
            "result": None,
            "kd": None,
            "sig_str": None,
        },
        "fighter2": {
            "name": None,
            "url": None,
            "result": None,
            "kd": None,
            "sig_str": None,
        },
    }


def extract_totals(soup):
    """Extract totals such as significant strikes, takedowns, and control."""
    # Totals section:
    # Located in the totals block for the fight.
    return {}


def extract_round_stats(soup):
    """Extract round-by-round stats as a list of dictionaries."""
    # Round stats:
    # Located in a round-by-round table or repeated section.
    return []


def extract_judges(soup):
    """Extract judge score details if present."""
    # Judge details:
    # Located in the scorecard / judges section.
    return []


def extract_fight_metadata(soup):
    """Extract fight-page metadata that is not part of the main stat blocks."""
    title = None
    if soup.title and soup.title.get_text(strip=True):
        title = soup.title.get_text(strip=True)

    return {
        "page_title": title,
        "source": "ufcstats",
    }


async def scrape_fight_page(context, fight_url):
    """Open one fight page, parse it, and return one completed fight dictionary."""
    page = await context.new_page()
    try:
        await page.goto(fight_url, wait_until="networkidle")
        html_content = await page.content()
        soup = BeautifulSoup(html_content, "html.parser")

        metadata = extract_fight_metadata(soup)
        metadata["url"] = fight_url

        return {
            "general_info": extract_general_info(soup),
            "fighters": extract_fighters(soup),
            "totals": extract_totals(soup),
            "round_stats": extract_round_stats(soup),
            "judges": extract_judges(soup),
            "metadata": metadata,
        }
    finally:
        await page.close()


async def scrape_individual_event(context, href, headline, date_iso, event_results, semaphore):
    """Open one event page, discover fight URLs, and return one event dictionary."""
    async with semaphore:
        event_page = await context.new_page()
        try:
            await event_page.goto(href, wait_until="networkidle")
            html = await event_page.content()
            soup = BeautifulSoup(html, "html.parser")

            fight_urls = set()
            rows = soup.find_all(
                "tr",
                class_="b-fight-details__table-row b-fight-details__table-row__hover js-fight-details-click",
            )
            for row in rows:
                fight_url = row.get("data-link")
                if fight_url:
                    fight_urls.add(urljoin(href, fight_url))

            fights = []
            for fight_url in sorted(fight_urls):
                fight_data = await scrape_fight_page(context, fight_url)
                fights.append(fight_data)

            event_results[href] = {
                "url": href,
                "headline": headline,
                "date": date_iso,
                "fights": fights,
            }
            return event_results[href]
        finally:
            await event_page.close()


async def extract_fight_stats_from_UFCstats():
    """Fetch the completed events page and schedule concurrent event scraping."""
    link = "http://ufcstats.com"
    event_results = {}
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_TABS)

    try:
        async with async_playwright() as p:
            print("Starting Async Playwright browser...")
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            main_page = await context.new_page()

            print(f"Navigating to main page: {link}")
            await main_page.goto(link, wait_until="networkidle")

            print("Waiting for fight rows to load...")
            await main_page.wait_for_selector("tr.b-statistics__table-row", timeout=10000)

            print("Parsing main HTML with BeautifulSoup...")
            main_html = await main_page.content()
            soup = BeautifulSoup(main_html, "html.parser")

            rows = soup.find_all("tr", class_="b-statistics__table-row")
            if not rows:
                await browser.close()
                return "no fight profile found"

            print(f"Found {len(rows)} potential events. Preparing concurrent queue...")

            tasks = []
            for row in rows:
                anchor = row.find("a", class_="b-link b-link_style_black")
                if not anchor:
                    continue
                href = anchor.get("href")
                if not href:
                    continue

                headline = anchor.get_text(strip=True)
                if headline == "":
                    headline = " ".join([text for text in anchor.stripped_strings])

                date_tag = row.find("span", class_="b-statistics__date")
                date_iso = None
                if date_tag:
                    date_text = date_tag.get_text(strip=True)
                    try:
                        date_iso = datetime.strptime(date_text, "%B %d, %Y").date().isoformat()
                    except ValueError:
                        date_iso = date_text

                task = asyncio.create_task(
                    scrape_individual_event(context, href, headline, date_iso, event_results, semaphore)
                )
                tasks.append(task)

            await main_page.close()
            print(f"Launching scraper across {MAX_CONCURRENT_TABS} simultaneous tabs...")
            await asyncio.gather(*tasks)
            await browser.close()

    except Exception as e:
        print(f"Error fetching with Playwright: {e}")
        return "no fight profile found"

    return event_results


if __name__ == "__main__":
    event_results = asyncio.run(extract_fight_stats_from_UFCstats())

    if isinstance(event_results, dict):
        print(f"\nTotal events processed completely: {len(event_results)}\n")
        for i, (url, data) in enumerate(event_results.items()):
            if i >= 5:
                break
            print(f"Event {i + 1}:")
            print(f" URL: {data['url']}")
            print(f" Headline: {data['headline']}")
            print(f" Date: {data['date']}")
            print(f" Fights: {len(data['fights'])}")
            print()
    else:
        print(event_results)


