import asyncio
from datetime import datetime
from urllib.parse import urljoin

import re
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


# Parse the Details section (judges for decisions, finish text for finishes).
def _extract_details_section(soup):
    """Parse the fight Details block from UFCStats HTML.

    On fight pages the structure is:

        <p class="b-fight-details__text">
          <i class="b-fight-details__text-item_first">
            <i class="b-fight-details__label">Details:</i>
          </i>
          <!-- either judge rows: -->
          <i class="b-fight-details__text-item"><span>Judge Name</span> 28 - 29.</i>
          <!-- or free-text finish details as text nodes after the wrapper -->
        </p>

    The label is nested inside text-item_first, so judges are siblings of that
    wrapper (inside the parent <p>), not siblings of the label itself.
    """
    details_label = None
    for label in soup.find_all("i", class_="b-fight-details__label"):
        if _normalize_label(label.get_text(" ", strip=True)) == "details":
            details_label = label
            break

    if details_label is None:
        return {"judges": [], "finish_details": None}

    judges = []
    finish_details = None

    # Prefer the Details paragraph; fall back to the label's immediate parent wrapper.
    container = details_label.find_parent("p", class_="b-fight-details__text")
    if container is None:
        container = details_label.parent

    if container is None:
        return {"judges": [], "finish_details": None}

    # Judge score rows live in sibling <i class="b-fight-details__text-item"> tags.
    for item in container.find_all("i", class_="b-fight-details__text-item"):
        classes = item.get("class") or []
        # BeautifulSoup class_ matches a single token; still skip the header wrapper
        # in case class lists combine both styles in the future.
        if "b-fight-details__text-item_first" in classes:
            continue

        judge_name_tag = item.find("span")
        judge_name = _clean_text(judge_name_tag) if judge_name_tag else None
        score_text = _clean_text(item)
        if judge_name and score_text:
            score_text = score_text.replace(judge_name, "", 1).strip()
            # Trailing period is common on UFCStats scorecards ("28 - 29.").
            if score_text.endswith("."):
                score_text = score_text[:-1].strip()
        if judge_name or score_text:
            judges.append({
                "judge": judge_name,
                "score": score_text or None,
            })

    # Finish details (KO/TKO/submission) are usually plain text in the same <p>
    # after the "Details:" wrapper when there are no judge rows.
    if not judges:
        parts = []
        for child in container.children:
            if getattr(child, "name", None) is None:
                text = str(child).strip()
                if text:
                    parts.append(text)
                continue
            classes = child.get("class") or []
            if "b-fight-details__text-item_first" in classes:
                # Strip the nested "Details:" label; keep any leftover finish text.
                label_text = _clean_text(details_label) or ""
                wrapper_text = _clean_text(child) or ""
                leftover = wrapper_text
                if label_text and leftover.lower().startswith(label_text.lower()):
                    leftover = leftover[len(label_text):].strip(" :")
                if leftover:
                    parts.append(leftover)
            elif "b-fight-details__text-item" not in classes:
                text = _clean_text(child)
                if text:
                    parts.append(text)
        finish_details = " ".join(parts).strip() or None

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
            "name": "",
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
    name_cells = soup.select("td.b-fight-details__table-col.l-page_align_left")
    print(len(name_cells))
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



def _get_table_header_text(table):
    """Return lowercased header text for a table, checking <thead> first."""
    header = table.find("thead")
    if header is None:
        header = table.find("tr")  # fallback: first row often IS the header row
    return (_clean_text(header) or "").lower()






# Extract round-by-round statistics into a list of structured dictionaries.
def extract_round_stats(soup):
    """Extract round-by-round stats as a list of dictionaries."""
    candidate_tables = soup.find_all("table", class_="b-fight-details__table js-fight-table")

    round_totals_table = None
    significant_strike_table = None
    for table in candidate_tables:
        header_row = table.find("tr")
        header_text = header_row.get_text(" ", strip=True).lower() if header_row else ""
        if "ctrl" in header_text:
            round_totals_table = table
        elif "head" in header_text and "body" in header_text and "leg" in header_text:
            significant_strike_table = table

    def _rows_by_round(table):
        """Walk a per-round table's rows in order, tracking the current round
        via standalone 'Round N' label rows, and return {round_number: [td,...]}."""
        rounds = {}
        if table is None:
            return rounds

        current_round = None
        for row in table.find_all("tr"):
            td_cells = row.find_all("td", class_="b-fight-details__table-col") or row.find_all("td")
            if td_cells:
                if current_round is not None:
                    rounds[current_round] = td_cells
                continue

            th_cells = row.find_all("th")
            if len(th_cells) == 1:
                match = re.search(r"round\s*(\d+)", th_cells[0].get_text(strip=True), re.IGNORECASE)
                if match:
                    current_round = int(match.group(1))
            # header row (multiple <th>) falls through and is ignored

        return rounds

    round_totals_rows = _rows_by_round(round_totals_table)
    significant_strike_rows = _rows_by_round(significant_strike_table)

    stat_labels = ["kd", "sig_str", "sig_str_percent", "total_str",
                   "td", "td_percent", "sub_att", "rev", "ctrl"]
    sig_labels = ["sig_str", "sig_str_percent", "head", "body",
                  "leg", "distance", "clinch", "ground"]

    round_stats = []
    for round_number in sorted(round_totals_rows.keys()):
        stat_columns = [
            c for c in round_totals_rows[round_number]
            if "l-page_align_left" not in c.get("class", [])
        ]
        round_dict = {"round": round_number, "fighter1": {}, "fighter2": {}}
        for stat_index, column in enumerate(stat_columns[:9]):
            paragraphs = column.find_all("p", class_="b-fight-details__table-text")
            if len(paragraphs) >= 2:
                round_dict["fighter1"][stat_labels[stat_index]] = _clean_text(paragraphs[0])
                round_dict["fighter2"][stat_labels[stat_index]] = _clean_text(paragraphs[1])

        if round_number in significant_strike_rows:
            sig_columns = [
                c for c in significant_strike_rows[round_number]
                if "l-page_align_left" not in c.get("class", [])
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
    link = "http://ufcstats.com/statistics/events/completed?page=all"
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


