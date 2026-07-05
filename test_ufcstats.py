import asyncio
from bs4 import BeautifulSoup
from datetime import datetime
from playwright.async_api import async_playwright

# Change this number to control how many tabs can open at the exact same time
MAX_CONCURRENT_TABS = 5

async def scrape_individual_event(context, href, headline, date_iso, fight_links, semaphore):
    """Worker function that handles opening a single tab concurrently."""
    # This semaphore ensures we never exceed MAX_CONCURRENT_TABS at once
    async with semaphore:
        print(f"[Start] Opening tab for: {headline}")
        event_page = await context.new_page()
        
        try:

            # Navigate to the individual fight event page
            await event_page.goto(href, wait_until="networkidle")
            html = await event_page.content()
            each_fight_general_table_link = set()

            # scrape with beautifulsoup
            soup = BeautifulSoup(html, "html.parser")
            # here is where will read all the html tags for the fight stats 
            rows = soup.find_all(
                'tr', class_='b-fight-details__table-row b-fight-details__table-row__hover js-fight-details-click'
                )
            # now we iterate through each row and extract the date we want to scrape gang
            fight_links = set()

            for row in rows:
                url = row.get("data-link")
                # debug code. if url is not found we just return, well url not found lol

                if not url:
                    print(f"[WARN] No link found in row for event: {headline}")
                    continue
                    #
                fight_page = await context.new_page()
                await fight_page.goto(url, wait_until='networkidle')
                fight_html = await fight_page.content()
                fight_soup = BeautifulSoup(fight_html, "html.parser")

                '''scraper implementation for individual fight page'''
                # we initialize fight details because it contains all the shit we need. then in fight details we extract the data we need.
                fight_details = fight_soup.find('div', class_='b-fight-details__fight')

              
                # now we extract the Method, Round, Time, Time Format, referee and details. 
                # since the html tags are fairly similar for the labels: b-fight-details__label and the value html tags being nested underneath the html tags with the class b-fight-details__text. we can use a for loop to iterate through the labels and extract the values.
                find_fight_weight_class_title = fight_details.find('i', class_='b-fight-details__fight-title').get_text(strip=True) if fight_details else "No fight details found"

                label = fight_details.find_all('i',class_='b-fight-details__label').get_text(strip=True) 
                value = label.next_sibling.strip().get_text(strip=True) if label else "no value found"



            




                #     print (f"[INFO] Found fight link: {url}")
                # if not url:
                #     print(f"[WARN] No links found in row for event: {headline}")

            
            # Store the successfully gathered data
            fight_links[href] = {
                "url": href,
                "headline": headline,
                "date": date_iso,
                # "inner_data": your_extracted_inner_data  # Add your data here
            }
            print(f"[Success] Processed and closed tab for: {headline}")
            
        except Exception as inner_e:
            print(f"[Error] Failed to scrape event {headline}: {inner_e}")
            
        finally:
            # Ensure the tab ALWAYS closes to save RAM
            await event_page.close()

async def extract_fight_stats_from_UFCstats():
    """Fetches the completed events page and schedules concurrent tab scraping."""
    link = 'http://ufcstats.com'
    fight_links = {}
    
    # Initialize the semaphore boundary
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
            
            rows = soup.find_all('tr', class_='b-statistics__table-row')
            if not rows:
                await browser.close()
                return "no fight profile found"
                
            print(f"Found {len(rows)} potential events. Preparing concurrent queue...")

            # We create a list to store all our background tasks
            tasks = []

            for row in rows:
                anchor = row.find('a', class_='b-link b-link_style_black')
                if not anchor:
                    continue
                href = anchor.get('href')
                if not href:
                    continue
                
                headline = anchor.get_text(strip=True)
                if headline == "":
                    headline = " ".join([t for t in anchor.stripped_strings])
                
                date_tag = row.find('span', class_='b-statistics__date')
                date_iso = None
                if date_tag:
                    date_text = date_tag.get_text(strip=True)
                    try:
                        date_iso = datetime.strptime(date_text, "%B %d, %Y").date().isoformat()
                    except ValueError:
                        date_iso = date_text

                # Queue up this event task to be processed asynchronously
                task = asyncio.create_task(
                    scrape_individual_event(context, href, headline, date_iso, fight_links, semaphore)
                )
                tasks.append(task)
            
            # Close the main index page tab since we don't need it anymore
            await main_page.close()
            
            # This line triggers all scheduled tasks to fire off simultaneously
            print(f"Launching scraper across {MAX_CONCURRENT_TABS} simultaneous tabs...")
            await asyncio.gather(*tasks)
            
            # Clean up the whole browser instance at the very end
            await browser.close()

    except Exception as e:
        print(f"Error fetching with Playwright: {e}")
        return "no fight profile found"

    return fight_links

if __name__ == "__main__":
    # Async scripts must be launched via the asyncio event loop runner
    fight_links = asyncio.run(extract_fight_stats_from_UFCstats())
    
    if isinstance(fight_links, dict):
        print(f"\nTotal links processed completely: {len(fight_links)}\n")
        for i, (url, data) in enumerate(fight_links.items()):
            if i >= 5:
                break
            print(f"Link {i+1}:")
            print(f" URL: {data['url']}")
            print(f" Headline: {data['headline']}")
            print(f" Date: {data['date']}")
            print()
    else:
        print(fight_links)




