"""
Current status:

✓ Detects last page
✓ Scrapes all fighter profile URLs
✓ Stores unique URLs in a set
✓ Extracts bio + performance stats
✓ Inserts fighter / fighter_bio_stats / fighter_performance_stats via Database layer
"""
import re
import requests
from bs4 import BeautifulSoup
import time
from datetime import datetime

from Database.db import get_connection
from Database.insert_fighters import insert_fighter
from Database.insert_fighter_bio import insert_fighter_bio
from Database.insert_fighter_performance_stats import insert_fighter_performance_stats


def safe_get(url, max_retries=4, backoff_factor=1.5, timeout=30):
    """Perform a GET with retries and exponential backoff on server/network errors.

    Returns the requests.Response on success (status_code == 200), or None on persistent failure.
    """
    attempt = 0
    while attempt < max_retries:
        try:
            resp = requests.get(url, timeout=timeout)
        except requests.exceptions.RequestException as e:
            wait = backoff_factor * (2 ** attempt)
            print(f"safe_get: network error fetching {url!r} (attempt {attempt+1}/{max_retries}): {e}; retrying in {wait:.1f}s")
            time.sleep(wait)
            attempt += 1
            continue

        if 500 <= resp.status_code < 600:
            wait = backoff_factor * (2 ** attempt)
            print(f"safe_get: server error {resp.status_code} for {url!r} (attempt {attempt+1}/{max_retries}); retrying in {wait:.1f}s")
            time.sleep(wait)
            attempt += 1
            continue

        if resp.status_code != 200:
            print(f"safe_get: unexpected status {resp.status_code} for {url!r}; skipping")
            return None

        return resp

    print(f"safe_get: failed to fetch {url!r} after {max_retries} attempts")
    return None


# connect to the database maine
connection = get_connection()


# hardcoding base URL and listing URL pattern for pagination iteration
BASE_URL = "https://www.ufc.com"
LISTING_URL = BASE_URL + "/athletes/all?gender=All&search=&page={page}"
fighter_cards = set()  # to store unique fighter profile URLs


'''we want to define all major helper functions in this file, 
and then we will have a main function that calls these helpers to do the actual scraping and database insertion. 
this way we can keep our code organized and modular, 
and we can also easily test each helper function individually before integrating them into the main scraper loop.'''
def get_fighter_name_from_soup(soup):
    Fighter_Name_section = soup.find("div", class_="hero-profile__info")
    # we do the 'is None' check because if fighter_name_section is not found then we wont pass name_tag which if we did pass would cause unboundedLocalError
    if Fighter_Name_section is None:
        print ("no fighter name found gang")
        return None
    else:
        Name_tag = (Fighter_Name_section.find("h1", class_="hero-profile__name")).get_text(strip=True)
        return Name_tag


'''this function extracts all the Bio data such height, weight, age and shit like that'''
def extract_bio_from_soup(soup):
    # this is the intitialization add logic to get rest of bio stats
    bio_section = soup.find("div", class_="c-bio__info-details")
    # dictionary where we store our bio stats
    bio_stats = {}
    if not bio_section:
        return bio_stats
    # We are gonna use 2 strats. Primary strategy: pair all label and text elements found in the bio section
    labels = [l.get_text(strip=True) for l in bio_section.find_all("div", class_="c-bio__label")]
    texts = [t.get_text(strip=True) for t in bio_section.find_all("div", class_="c-bio__text")]
# iteratese through the labels and texts in parallel, 
# pairing them based on their order of appearance in the HTML
    for i, label in enumerate(labels):
        value = texts[i] if i < len(texts) else None
        clean_key = label.lower().replace(" ", "_")
        if clean_key == "octagon_debut" and value:
            date_str = value.strip()

            try:
                # Replaces any shortened month dot typos if needed and strips whitespace
                # date_str = value.strip()
                parsed_date = datetime.strptime(date_str, "%b. %d, %Y").date()
                value = parsed_date.isoformat()  # Saves as "yyyy-mm-dd" string
            except ValueError:
                try:
                    parsed_date = datetime.strptime(date_str, "%b %d, %Y").date()
                    value = parsed_date.isoformat()
                except ValueError:
                    pass  # Keeps the original string if parsing completely fails
                    
        bio_stats[clean_key] = value

    # Fallback: if labels weren't found as separate elements, try row-wise extraction
    if not bio_stats:
        for row in bio_section.find_all("div", class_="c-bio__row--3col"):
            label_tag = row.find("div", class_="c-bio__label")
            text_tag = row.find("div", class_="c-bio__text")
            label = label_tag.get_text(strip=True) if label_tag else None
            value = text_tag.get_text(strip=True) if text_tag else None
            if label or value:
                bio_stats[label] = value

    return bio_stats


'''this function extracts the performance stats from the carousel section of the fighter profile page, returning a list of dictionaries with label-value pairs for each stat.'''
def extract_performance_stats_from_soup(soup):
    carousel = soup.find(
        "div",
        class_="c-carousel--multiple__content carousel__multiple-items stats-records-inner-wrap",
        attrs={"data-carousel": "athlete"},
        )
    if not carousel:
        return {}
    
    # dictionary to store performance stats in
    performance_stats = {}

    two_column = carousel.find_all('div', class_='stats-records stats-records--two-column')
    three_column = carousel.find_all('div', class_='stats-records stats-records--three-column')


# runner for two_column
    for idx, block in enumerate(two_column):
        if idx in [0,1]:
            labels = block.find_all('dt', class_='c-overlap__stats-text')
            values = block.find_all('dd', class_='c-overlap__stats-value')

            for label, value in zip(labels, values):
                key = label.get_text(strip=True)
                val = value.get_text(strip=True)
                performance_stats[key] = val

        elif idx in[2,3]:
            labels = block.find_all('div', class_='c-stat-compare__label')
            suffixes = block.find_all('div', class_='c-stat-compare__label-suffix')
            values = block.find_all('div', class_='c-stat-compare__number')



            for i in range(len(labels)):
                key = labels[i].get_text(strip=True)
                val = values[i].get_text(strip=True) if i < len(values) else ""
                suf = suffixes[i].get_text(strip=True) if i < len(suffixes) else ""
                performance_stats[key + suf] = val

    # runner for three_column
    for idx, block in enumerate(three_column):

        if idx in [0,2]:
              title_element = block.find('div', class_='c-stat-3bar__title')
              tit = title_element.get_text(strip=True) if title_element else ""
              labels = block.find_all('div', class_='c-stat-3bar__label')
              values = block.find_all('div', class_='c-stat-3bar__value')

              for label, value in zip(labels, values):

                clean_label = label.get_text(strip=True).lower()
                raw_val = value.get_text(strip=True)
                match = re.search(r"(\d+)\s*\((\d+)%\)", raw_val)
                
                if match:
                    count = int(match.group(1))
                    percent = int(match.group(2))
                else:
                    count, percent = 0, 0  
                
                performance_stats[f"{clean_label}_count"] = count
                performance_stats[f"{clean_label}_percent"] = percent



        elif idx in[1]:
            title_element = block.find('h2', class_='e-t5')
            tit = title_element.get_text(strip=True) if title_element else ""

            
            head_title = block.find('g', id='e-stat-body_x5F__x5F_head-txt')
            body_title = block.find('g', id='e-stat-body_x5F__x5F_body-txt')
            leg_title = block.find('g', id='e-stat-body_x5F__x5F_leg-txt')
            
            head_raw = head_title.get_text(strip=True) if head_title else ""
            body_raw = body_title.get_text(strip=True) if body_title else ""
            leg_raw = leg_title.get_text(strip=True) if leg_title else ""


            def parse_stat(raw_string):
                match = re.match(r"(\d+)%(\d+)", raw_string)
                if match:
                    return int(match.group(1)), int(match.group(2))
                return 0, 0  # Default fallback if layout changes or is missing
            
            head_pct, head_cnt = parse_stat(head_raw)
            body_pct, body_cnt = parse_stat(body_raw)
            leg_pct, leg_cnt = parse_stat(leg_raw)
            
            performance_stats['head_percent'] = head_pct
            performance_stats['head_count'] = head_cnt
            performance_stats['body_percent'] = body_pct
            performance_stats['body_count'] = body_cnt
            performance_stats['leg_percent'] = leg_pct
            performance_stats['leg_count'] = leg_cnt

    return performance_stats


# known last page from previous run
KNOWN_LAST_PAGE = 286
# function to merge scraping and last page detection in one pass
def merge_scrape_and_find_last_page(start_page=1, delay=1):
    page = start_page
    last_page = start_page
    all_fighter_links = set()
    last_soup = None
# iterate through pages until we find no more fighter cards or no "Load More" button
    while True:
        url = LISTING_URL.format(page=page)
        res = requests.get(url)
        res.raise_for_status()
        soup = BeautifulSoup(res.content, "html.parser")
        print(f"Fetching page {page}")
# find all fighter cards on the current page using the HTML container (c-listing-athlete-flipcard__action) in the UFC website
        fighter_cards = soup.find_all(
            "div",
            class_="c-listing-athlete-flipcard__action"
        )
        # if we find no fighter cards, we have likely gone past the last page
        if not fighter_cards:
            break
# print page number and number of fighter cards found for debugging
        print("Page", page, "fighters:", len(fighter_cards))
        last_page = page
        last_soup = soup
        # extract links while we are here. also while we are here, 
        # we will extract fighter bio info and store it in the database, so we dont have to iterate through the website again to get the bios after we find the last page.
        # this way we can do everything in one pass.
        counter = 1
        for card in fighter_cards:
            link = card.find('a')
            if link and link.get('href'):
                href = link.get('href')
                full_url = BASE_URL + href
                print("Found fighter URL:", full_url)  # debug print
                all_fighter_links.add(full_url)
                print("Processing fighter URL:", full_url)  # debug print
                fighter_res = requests.get(full_url)
                fighter_res.raise_for_status()
                fighter_soup = BeautifulSoup(fighter_res.content, "html.parser")
                name = get_fighter_name_from_soup(fighter_soup)
                print(f"Processing fighter #{counter}: {name}")
                bio_info = extract_bio_from_soup(fighter_soup)
                performance_info = extract_performance_stats_from_soup(fighter_soup)
                print("Presenting Fighter:", name, "'s", "Stats")
                print("Extracted bio info:", bio_info)  # debug print
                print("Extracted performance info:", performance_info)  # debug print

                '''debugginf, wanna see the keys and how they formatted'''
                # for key in performance_info:
                #     print(key)

                # for key in bio_info:
                #     print(key)
            

                if name:
                    fighter_dict = {"fighter_name": name, "fighter_url": full_url}
                    print(f"Processing fighter #{counter}: {name}")
                    try:
                        print("ABOUT TO INSERT:", name)
                        fighter_id = insert_fighter(connection, fighter_dict)
                        print("fighter_id returned:", fighter_id)
                        print("INSERT SUCCESS")
                        insert_fighter_bio(connection, fighter_id, bio_info)
                        print("BIO INSERT SUCCESS")
                        insert_fighter_performance_stats(connection, fighter_id, performance_info)
                        print("PERFORMANCE INSERT SUCCESS")
                        print(f"Saved fighter {name} as fighter_id={fighter_id}")
                        counter += 1
                    except Exception as db_exc:
                        print(f"Error saving fighter {name}: {db_exc}")

        # determine if there is another page via "Load More"
        next_link_exists = any(
            "load more" in a.get_text(strip=True).lower()
            for a in soup.select('a[href*="page="]')
        )
        if not next_link_exists:
            break
# iterate to next page
        page += 1
        time.sleep(delay)

    return all_fighter_links, last_page, last_soup


# for testing just the merged function
if __name__ == "__main__":
    # run merged scraper
    all_fighter_links, last_page, last_soup = merge_scrape_and_find_last_page()
# print results and detect updates
    print(f"Last page reached: {last_page}")
    print(f"Total fighters found: {len(all_fighter_links)}")

    # detect updates: either last page number changed, or a 'Load More' exists on the known last page
    if last_page != KNOWN_LAST_PAGE:
        print("Website updated: last page changed from", KNOWN_LAST_PAGE, "to", last_page)
    else:
        # check for a Load More button on the known last page
        load_more_exists = False
        if last_soup is None:
            # fetch the known last page to inspect
            last_url = LISTING_URL.format(page=KNOWN_LAST_PAGE)
            res = requests.get(last_url)
            res.raise_for_status()
            last_soup = BeautifulSoup(res.content, "html.parser")
# while parsing the known last page, check if a "Load More" button exists, which would indicate an update
        for a in last_soup.select('a[href*="page="]'):
            if "load more" in a.get_text(strip=True).lower():
                load_more_exists = True
                break
# final update detection logic
        if load_more_exists:
            print("Website updated: 'Load More' button exists on page", KNOWN_LAST_PAGE)
        else:
            print("No update: page", KNOWN_LAST_PAGE, "is still the final page")
