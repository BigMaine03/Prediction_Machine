import asyncio
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# Change this number to control how many tabs that can open at the same time.
MAX_CONCURRENT_TABS = 5


# Clean and normalize text extracted from BeautifulSoup nodes.
def _clean_text(value):
    if value is None:
        return None
    if hasattr(value, "get_text"):
        return value.get_text(" ", strip=True)
    return str(value).strip()






# Normalize label text so HTML labels can be matched consistently.
def _normalize_label(value):
    if value is None:
        return ""
    return " ".join(str(value).strip().lower().replace(":", "").split())





# Extract the value associated with a label by reading the next sibling text content.
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


# Parse the Details section based on the HTML structure beneath the label.
def _extract_details_section(soup):
    details_label = None
    for label in soup.find_all("i", class_="b-fight-details__label"):
        if _normalize_label(label.get_text(" ", strip=True)) == "details":
            details_label = label
            break

    if details_label is None:
        return {"judges": [], "finish_details": None}

    judges = []
    finish_details = None

    for sibling in details_label.next_siblings:
        if getattr(sibling, "name", None) != "i":
            continue

        if "b-fight-details__text-item" in sibling.get("class", []):
            judge_name_tag = sibling.find("span")
            judge_name = _clean_text(judge_name_tag) if judge_name_tag else None
            score_text = _clean_text(sibling)
            if judge_name and score_text:
                score_text = score_text.replace(judge_name, "", 1).strip()
            if judge_name or score_text:
                judges.append({
                    "judge": judge_name,
                    "score": score_text or None,
                })
        elif "b-fight-details__text-item_first" in sibling.get("class", []):
            finish_details = _clean_text(sibling)

    return {"judges": judges, "finish_details": finish_details}


# Extract the main match metadata for one fight, including result details and labels.
def extract_general_info(soup):
    """Extract general fight info such as weight class, method, round, and time."""
    # Weight class is stored in the fight-title tag for the main bout summary.
    fight_details = soup.find("div", class_="b-fight-details__fight") or soup
    weight_class = _clean_text(fight_details.find("i", class_="b-fight-details__fight-title"))

    # Method, round, time, time format, and referee are stored as label/value pairs.
    details_data = _extract_details_section(soup)
    general_info = {
        "weight_class": weight_class,
        "method": _extract_label_value(soup, "Method"),
        "round": _extract_label_value(soup, "Round"),
        "time": _extract_label_value(soup, "Time"),
        "time_format": _extract_label_value(soup, "Time format"),
        "referee": _extract_label_value(soup, "Referee"),
        "judges": details_data["judges"],
        "finish_details": details_data["finish_details"],
    }

    return general_info


# Extract fighter-level details for both competitors in the fight card.
def extract_fighters(soup):
    """Extract fighter-level information for both sides of the fight."""
    # The fighter totals section is the second section in the fight-details layout.
    sections = soup.find_all("section", class_="b-fight-details__section js-fight-section")
    fighter_section = sections[1] if len(sections) > 1 else soup

    fighter_profile_links = set()
    fighters = {
        "fighter1": {
            "name": None,
            "url": None,
            "result": None,
            "kd": None,
            "sig_str": None,
            "sig_str_percent": None,
            "total_str": None,
            "td": None,
            "td_percent": None,
            "sub_att": None,
            "rev": None,
            "ctrl": None,
        },
        "fighter2": {
            "name": None,
            "url": None,
            "result": None,
            "kd": None,
            "sig_str": None,
            "sig_str_percent": None,
            "total_str": None,
            "td": None,
            "td_percent": None,
            "sub_att": None,
            "rev": None,
            "ctrl": None,
        },
        "fighter_profile_links": fighter_profile_links,
    }

    # Fighter names and profile URLs are stored in left-aligned name cells.
    name_cells = fighter_section.find_all("td", class_="b-fight-details__table-col l-page_align_left")
    for index, cell in enumerate(name_cells[:2]):
        fighter_key = f"fighter{index + 1}"
        link = cell.find("a", class_="b-link b-link_style_black")
        if link is not None:
            fighter_url = link.get("href")
            if fighter_url:
                fighter_profile_links.add(fighter_url)
            fighters[fighter_key]["name"] = _clean_text(link)
            fighters[fighter_key]["url"] = fighter_url
        else:
            fighters[fighter_key]["name"] = _clean_text(cell)

    # The remaining nine stat columns each contain two table-text paragraphs for fighter 1 and fighter 2.
    stat_labels = [
        "kd",
        "sig_str",
        "sig_str_percent",
        "total_str",
        "td",
        "td_percent",
        "sub_att",
        "rev",
        "ctrl",
    ]

    for row in fighter_section.find_all("tr"):
        stat_columns = [
            col for col in row.find_all("td", class_="b-fight-details__table-col")
            if "l-page_align_left" not in col.get("class", [])
        ]
        if len(stat_columns) < 9:
            continue

        for index, column in enumerate(stat_columns[:9]):
            paragraphs = column.find_all("p", class_="b-fight-details__table-text")
            if len(paragraphs) >= 2:
                fighters["fighter1"][stat_labels[index]] = _clean_text(paragraphs[0])
                fighters["fighter2"][stat_labels[index]] = _clean_text(paragraphs[1])
            elif len(paragraphs) == 1:
                fighters["fighter1"][stat_labels[index]] = _clean_text(paragraphs[0])
                fighters["fighter2"][stat_labels[index]] = None
        break

    return fighters


# Extract the totals section for the fight, such as strike and control stats.
def extract_totals(soup):
    """Extract totals such as significant strikes, takedowns, and control."""
    # The total significant strike section uses a table with two table-text paragraphs per stat.
    totals = {"fighter1": {}, "fighter2": {}}
    significant_strike_table = soup.find("table", style="width:745px")
    if significant_strike_table is None:
        return totals

    stat_labels = [
        "sig_str",
        "sig_str_percent",
        "head",
        "body",
        "leg",
        "distance",
        "clinch",
        "ground",
    ]

    stat_columns = [
        column for column in significant_strike_table.find_all("td")
        if column.find_all("p", class_="b-fight-details__table-text")
    ]

    for index, column in enumerate(stat_columns[:8]):
        paragraphs = column.find_all("p", class_="b-fight-details__table-text")
        if len(paragraphs) >= 2:
            totals["fighter1"][stat_labels[index]] = _clean_text(paragraphs[0])
            totals["fighter2"][stat_labels[index]] = _clean_text(paragraphs[1])

    return {"significant_strikes": totals}





# Extract round-by-round statistics into a list of structured dictionaries.
def extract_round_stats(soup):
    """Extract round-by-round stats as a list of dictionaries."""
    # The round-by-round totals table is the third fight-details table in the page layout.
    fight_tables = soup.find_all("table", class_="b-fight-details__table js-fight-table")
    round_table = fight_tables[2] if len(fight_tables) > 2 else None

    # The per-round significant strike section is the fifth section in the fight-details layout.
    sections = soup.find_all("section", class_="b-fight-details__section js-fight-section")
    significant_strike_section = sections[4] if len(sections) > 4 else None

    round_rows = []
    if round_table is not None:
        round_rows = [
            row for row in round_table.find_all("tr", class_="b-fight-details__table-row")
            if row.find_all("td", class_="b-fight-details__table-col")
        ]
    elif soup.find_all("tr", class_="b-fight-details__table-row"):
        round_rows = [
            row for row in soup.find_all("tr", class_="b-fight-details__table-row")
            if row.find_all("td")
        ]

    stat_labels = [
        "kd",
        "sig_str",
        "sig_str_percent",
        "total_str",
        "td",
        "td_percent",
        "sub_att",
        "rev",
        "ctrl",
    ]

    significant_strike_rows = []
    if significant_strike_section is not None:
        significant_strike_rows = [
            row for row in significant_strike_section.find_all("tr", class_="b-fight-details__table-row")
            if row.find_all("td")
        ]

    round_stats = []
    for index, row in enumerate(round_rows):
        stat_columns = [
            column for column in row.find_all("td", class_="b-fight-details__table-col")
            if "l-page_align_left" not in column.get("class", [])
        ]
        round_dict = {
            "round": index + 1,
            "fighter1": {},
            "fighter2": {},
        }

        for stat_index, column in enumerate(stat_columns[:9]):
            paragraphs = column.find_all("p", class_="b-fight-details__table-text")
            if len(paragraphs) >= 2:
                round_dict["fighter1"][stat_labels[stat_index]] = _clean_text(paragraphs[0])
                round_dict["fighter2"][stat_labels[stat_index]] = _clean_text(paragraphs[1])

        if index < len(significant_strike_rows):
            sig_columns = significant_strike_rows[index].find_all("td")
            sig_labels = [
                "sig_str",
                "sig_str_percent",
                "head",
                "body",
                "leg",
                "distance",
                "clinch",
                "ground",
            ]
            significant_strikes = {"fighter1": {}, "fighter2": {}}
            for sig_index, column in enumerate(sig_columns[:8]):
                paragraphs = column.find_all("p", class_="b-fight-details__table-text")
                if len(paragraphs) >= 2:
                    significant_strikes["fighter1"][sig_labels[sig_index]] = _clean_text(paragraphs[0])
                    significant_strikes["fighter2"][sig_labels[sig_index]] = _clean_text(paragraphs[1])
            round_dict["significant_strikes"] = significant_strikes

        round_stats.append(round_dict)

    return round_stats


# Extract judge score details from any scorecard or judging section.
def extract_judges(soup):
    """Extract judge score details if present."""
    return _extract_details_section(soup)["judges"]


# Extract page-level metadata for the fight page, such as the title.
def extract_fight_metadata(soup):
    """Extract fight-page metadata that is not part of the main stat blocks."""
    title = None
    if soup.title and soup.title.get_text(strip=True):
        title = soup.title.get_text(strip=True)

    return {
        "page_title": title,
        "source": "ufcstats",
    }


# Open a single fight page, parse it, and return one structured fight dictionary.
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


# Open one event page, gather every fight URL, and return the event payload with fight data.
async def scrape_individual_event(context, href, headline, date_iso,location_text, event_results, semaphore):
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
                "location": location_text,
            }
            return event_results[href]
        finally:
            await event_page.close()


# Scrape the completed events listing and kick off concurrent event processing.
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


                location_tag = row.find('td', class_='b-statistics__table-col b-statistics__table-col_style_big-top-padding')
                location_text = None if location_tag == "" else location_tag
                if location_tag:
                    location_text = location_tag.get_text(strip=True)


                

                task = asyncio.create_task(
                    scrape_individual_event(context, href, headline, date_iso, location_text, event_results, semaphore)
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
            print(f"Event {i + 1}:")
            print(f" URL: {data['url']}")
            print(f" Headline: {data['headline']}")
            print(f" Date: {data['date']}")
            print(f" Location: {data['location']}")
            print(f" Fights: {len(data['fights'])}")
            print()
    else:
        print(event_results)


