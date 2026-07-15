
# created a scrape bot that fetches the first fighter profile from the listing page and extracts 
# the bio data as a list of (label, value) pairs.
# now also extracts the perforamance stats from the carousel section of the same profile page, returning a list of dictionaries with label-value pairs for each stat.


"""what i must do now is to add another function that exctracts stats from another section 
of the same page, which is in a carousel section with class
 "c-carousel--multiple__content carousel__multiple-items stats-records-inner-wrap"""
"""i have also found what tag needs to be extracted from that section, 
which are in the form of <dt class="c-overlap__stats-text">...</dt> and
 <dd class="c-overlap__stats-value">...</dd> for the text and value respectively."""
from datetime import datetime
import re 
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.ufc.com"
LISTING_URL = BASE_URL + "/athletes/all?gender=All&search=&page=1"




'''function to get the URL of the first fighter profile from the listing page'''
"""UPDATE that need to be done: make function recursive so it pulls all links from pages and stores it in a set
additionally. Thinking about how to integrate this function into the 'merge_scrape_and_find_last_page' funtion so machine doesnt have to iterate through website twice.
as it goes through the pages to check for last page, it will also retireve all athlete profile links available"""
def get_first_profile_url():
    res = requests.get(LISTING_URL)
    res.raise_for_status()
    soup = BeautifulSoup(res.content, "html.parser")
    card = soup.find("div", class_="c-listing-athlete-flipcard__action")
    if not card:
        return None
    # this is where the recursive shits gotta be added 
    unique_fighter_urls = {}
    a = card.find('a')
    if not a or not a.get('href'):
        return None
    # unique_fighter_urls[a.get('href')] = BASE_URL + a.get('href')
    return BASE_URL + a.get('href')




'''This function takes the soup of a fighter profile page and extracts 
the bio information as a dictionary of label-value pairs. 
It uses two strategies: first, it tries to pair labels and values based on their order in the HTML,
and if that fails, it falls back to a row-wise extraction method.'''
def extract_bio_from_soup(soup):
    bio_section = soup.find("div", class_="c-bio__info-details")
    result = {}
    if not bio_section:
        return result
    # Primary strategy: pair all label and text elements found in the bio section
    labels = [l.get_text(strip=True) for l in bio_section.find_all("div", class_="c-bio__label")]
    texts = [t.get_text(strip=True) for t in bio_section.find_all("div", class_="c-bio__text")]
# iteratese through the labels and texts in parallel, 
# pairing them based on their order of appearance in the HTML
    for i, label in enumerate(labels):
        value = texts[i] if i < len(texts) else None
        result[label] = value

    # Fallback: if labels weren't found as separate elements, try row-wise extraction
    if not result:
        for row in bio_section.find_all("div", class_="c-bio__row--3col"):
            label_tag = row.find("div", class_="c-bio__label")
            text_tag = row.find("div", class_="c-bio__text")
            label = label_tag.get_text(strip=True) if label_tag else None
            value = text_tag.get_text(strip=True) if text_tag else None
            if label or value:
                result[label] = value

    return result
















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





# runner for two column
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


    # runner for three column
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



















'''now we create a third function that extracts the fighters record. more specifically who the fought and that fights outcome
This function should primarily look for the 'c-card-event--athlete-results__plaque win' tagline the represents the outcome of the fight. if there isn't the outcome tagline then return no outcome found. The function should also look for the
 ‘<a class="button" href="?page=4" title="Load more items" 'rel="next”>’ this tagline represents the “load more” button and we want the function to detect it and access it so we can get the fighter record stats. '''
# <div id="athlete-record" class="athlete-record">…</div>
# this is the container for all the fighters wins and losses and shit


def get_fighter_record_from_soup(soup):
    """Extract fight records from the athlete record listing.

    Returns dict: { 'records': [ ... ], 'more': bool, 'message': str }
    Each record: {winner_href, loser_href, date, result_label, result_text}
    """
    result = {}

    # try to locate the infinite-scroll wrapper
    container = soup.find("div", class_="views-infinite-scroll-content-wrapper clearfix")
    if not container:
        container = soup.find("div", class_="views-infinite-scroll-content-wrapper")

    if not container:
        result["message"] = "no records container found"
        return result

    for li in container.find_all("li", class_="l-listing__item"):
        winner_href = None
        loser_href = None

        # locate winner (blue win) and loser (red loss) image blocks and their links
        for div in li.find_all("div", class_=True):
            classes = " ".join(div.get("class") or [])
            if "blue-image" in classes and "win" in classes:
                a = div.find("a")
                if a and a.get("href"):
                    winner_href = a.get("href")
            if "red-image" in classes and "loss" in classes:
                a = div.find("a")
                if a and a.get("href"):
                    loser_href = a.get("href")

        date_tag = li.find("div", class_="c-card-event--athlete-results__date")
        date_text = date_tag.get_text(strip=True) if date_tag else None

        label_tag = li.find("div", class_="c-card-event--athlete-results__result-label")
        label_text = label_tag.get_text(strip=True) if label_tag else None

        result_tag = li.find("div", class_="c-card-event--athlete-results__result-text")
        result_text = result_tag.get_text(strip=True) if result_tag else None

        record = {
            "winner_href": winner_href,
            "loser_href": loser_href,
            "date": date_text,
            "result_label": label_text,
            "result_text": result_text,
        }
        result["records"].append(record)

    # detect load-more link
    load_more = container.find("a", class_="button", attrs={"rel": "next"})
    if load_more and ("Load more" in (load_more.get("title") or "") or "Load more" in (load_more.get_text() or "")):
        result["more"] = True
        result["message"] = "load more available"
    else:
        result["more"] = False
        result["message"] = "all records returned and reached the end"

    return result










def get_fighter_name_from_soup(soup):
    Fighter_Name_section = soup.find("div", class_="hero-profile__info")
    if Fighter_Name_section:
        Name_tag = Fighter_Name_section.find("h1", class_="hero-profile__name")
    print("Presenting fighter: ", Name_tag.text, " stats")































# main function to fetch the first profile and print the bio data list
def main():
    profile_url = get_first_profile_url()
    if not profile_url:
        print("No profile URL found on listing page.")
        return
    print("Fetching:", profile_url)
    res = requests.get(profile_url)
    res.raise_for_status()
    soup = BeautifulSoup(res.content, "html.parser")
    # all the values that the function gets is stored in these three un-initialized variables
    name = get_fighter_name_from_soup(soup)
    fighter_bio_data = extract_bio_from_soup(soup)
    fighter_performance_stats = extract_performance_stats_from_soup(soup)
    # fighter_fight_record = get_fighter_record_from_soup(soup)
    # print only the bio data list (first fighter)
    print(fighter_bio_data)
    print(fighter_performance_stats)
    # print(fighter_fight_record)

if __name__ == "__main__":
    main()



