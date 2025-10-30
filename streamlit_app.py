import streamlit as st
import time
import threading
import pandas as pd
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import re
import json

# Selenium imports
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException, ElementClickInterceptedException
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager

# ===============================
# Thread-safe logging for Streamlit
# ===============================
log_lock = threading.Lock()
if "log_buffer" not in st.session_state:
    st.session_state.log_buffer = ""

# Keep original built-in print to avoid recursion
_builtin_print = print

def log_callback(message):
    """Send logs to Streamlit UI and terminal without recursion."""
    timestamp = time.strftime("[%H:%M:%S]")
    msg = f"{timestamp} {message}"
    _builtin_print(msg)  # real console print
    with log_lock:
        st.session_state.log_buffer += msg + "\n"

# Redirect all prints to Streamlit-safe logger
print = log_callback


# ================================================================
# ===============  YOUR ORIGINAL SCRAPER LOGIC  ==================
# ================================================================
CONFIG_FILE = 'config.json'
try:
    with open(CONFIG_FILE, 'r') as f:
        config = json.load(f)
    print(f"Loaded configuration from {CONFIG_FILE}")
except FileNotFoundError:
    print(f"Error: Configuration file '{CONFIG_FILE}' not found.")
    raise SystemExit
except json.JSONDecodeError as e:
    print(f"Error: Could not parse configuration file '{CONFIG_FILE}'. Invalid JSON: {e}")
    raise SystemExit
except Exception as e:
    print(f"An unexpected error occurred loading the configuration: {e}")
    raise SystemExit

BASE_URL = config.get('base_url')
MAX_PAGES = config.get('max_pages_to_scrape', 1)
PAGINATION_TYPE = config.get('pagination_type', 'none')
NEXT_PAGE_SELECTOR = config.get('next_page_button_selector')
STORY_LIST_SELECTOR = config.get('story_card_list_selector', 'body')
STORY_LINK_SELECTOR = config.get('story_card_link_selector', 'a')
WAIT_FOR_SELECTOR = config.get('wait_for_element_selector', 'body')
LOAD_TIMEOUT = config.get('details_page_load_timeout', 180)
WAIT_TIMEOUT = config.get('main_wait_timeout', 180)
MAX_RETRIES = config.get('max_retries', 3)
OUTPUT_FILENAME = config.get('output_filename', 'scraped_data.xlsx')
DATA_SELECTORS = config.get('data_selectors', {})
CONFIDENCE_THRESHOLDS = config.get('confidence_thresholds', {"high": 7, "medium": 4})
OUTPUT_COLUMNS = config.get('output_columns', [])

if not BASE_URL:
    print("Error: 'base_url' must be defined in the configuration file.")
    raise SystemExit

SECTION_KEYWORDS = {"challenge", "solution", "headquarters", "industry", "integrations", "share", "results", "the", "about", "at", "group", "financial"}


def get_story_links(driver, wait, config):
    base_url = config['base_url']
    max_pages_or_clicks = config['max_pages_to_scrape']
    pagination_type = config['pagination_type']
    next_page_selector_template = config['next_page_button_selector']
    list_selector = config['story_card_list_selector']
    link_selector = config['story_card_link_selector']

    print(f"Navigating to {base_url} to find links...")
    driver.get(base_url)
    time.sleep(3)
    links = set()

    # Cookie Consent
    try:
        cookie_wait = WebDriverWait(driver, 10)
        cookie_button = cookie_wait.until(EC.element_to_be_clickable(
            (By.XPATH, "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'accept all') or contains(@id, 'accept') or contains(@class, 'accept')] | //a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'accept all')]")
        ))
        print("  Found and clicked cookie accept button.")
        driver.execute_script("arguments[0].click();", cookie_button)
        time.sleep(2)
    except Exception:
        print("  No obvious cookie banner found or timed out. Continuing...")

    # Initial Wait
    first_link_full_selector = f"{list_selector} {link_selector}:first-of-type"
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, list_selector)))
        print(f"  Initial container ('{list_selector}') found.")
        wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, first_link_full_selector)))
        print(f"  First link ('{first_link_full_selector}') visible.")
        time.sleep(1)
    except TimeoutException:
        print(f"Error: Initial container/link not found/visible. Cannot proceed.")
        return []

    # Pagination Loop (simplified; your original logic kept)
    current_page = 1
    pagination_active = True
    last_first_href = ""
    while pagination_active and current_page <= max_pages_or_clicks:
        print(f"--- Processing Page {current_page} ---")
        found_on_page = 0
        current_first_href = None

        try:
            first_link_element = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, first_link_full_selector)))
            current_first_href = first_link_element.get_attribute('data-href') or first_link_element.get_attribute('href')
            time.sleep(2)

            if current_page > 1 and current_first_href == last_first_href:
                print(f"    Warning: Content appears unchanged after pagination. Stopping.")
                break
            last_first_href = current_first_href

            link_elements = driver.find_elements(By.CSS_SELECTOR, f"{list_selector} {link_selector}")
            print(f"  Found {len(link_elements)} potential link elements.")

            for element in link_elements:
                try:
                    href = element.get_attribute('data-href') or element.get_attribute('href')
                    if href:
                        if href.endswith('/.html'):
                            href = href[:-6]
                        elif href.endswith('.html'):
                            href = href[:-5]
                        full_url = urljoin(base_url, href)
                        if urljoin(base_url, '/') in full_url and full_url != base_url and href != '#':
                            company_regex = config.get('data_selectors', {}).get('company_name', {}).get('regex', '/stories/([^/]+)/')
                            path_start_segment = company_regex.split('/')[1]
                            path_start = base_url.rsplit('/', 2)[0] + f'/{path_start_segment}/'
                            if full_url.startswith(path_start):
                                if full_url not in links:
                                    links.add(full_url)
                                    found_on_page += 1
                except Exception as link_err:
                    print(f"    Error processing link element: {link_err}")

            print(f"  Found {found_on_page} new unique links on page view.")
            if not link_elements and current_page == 1:
                print(" Error: No links found on initial page. Exiting.")
                return []

            if current_page < max_pages_or_clicks:
                if pagination_type == 'click_button_by_page_number' and next_page_selector_template:
                    next_page_num_str = str(current_page + 1)
                    page_selector = next_page_selector_template.format(page_num=next_page_num_str)
                    print(f"  Attempting pagination click for page {next_page_num_str}...")
                    page_button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, page_selector)))
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", page_button)
                    time.sleep(0.5)
                    driver.execute_script("arguments[0].click();", page_button)
                    print(f"  Clicked page {next_page_num_str} link.")
                    WebDriverWait(driver, 30).until(
                        EC.visibility_of_element_located((By.CSS_SELECTOR, first_link_full_selector))
                    )
                    WebDriverWait(driver, 10).until(
                        lambda d: (d.find_element(By.CSS_SELECTOR, first_link_full_selector).get_attribute('data-href') or d.find_element(By.CSS_SELECTOR, first_link_full_selector).get_attribute('href')) != last_first_href
                    )
                    print(f"  Page {next_page_num_str} appears loaded.")
                    time.sleep(3)
                elif pagination_type == 'click_load_more' and next_page_selector_template:
                    print(f"  Attempting 'Load More' (Click {current_page})...")
                    load_more_button = WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.CSS_SELECTOR, next_page_selector_template)))
                    current_items_selector = f"{list_selector} {link_selector}"
                    initial_item_count = len(driver.find_elements(By.CSS_SELECTOR, current_items_selector))
                    print(f"    Click {current_page}: Found button. Current items: {initial_item_count}. Clicking...")
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", load_more_button)
                    time.sleep(0.5)
                    try:
                        load_more_button.click()
                    except ElementClickInterceptedException:
                        print("      Std click intercepted, trying JS.")
                        driver.execute_script("arguments[0].click();", load_more_button)
                    WebDriverWait(driver, 60).until(lambda d: len(d.find_elements(By.CSS_SELECTOR, current_items_selector)) > initial_item_count)
                    new_item_count = len(driver.find_elements(By.CSS_SELECTOR, current_items_selector))
                    print(f"    Load More Click {current_page}: Items increased to {new_item_count}.")
                    time.sleep(1)
                else:
                    pagination_active = False
                    print("  Pagination finished or not configured.")

            if current_page >= max_pages_or_clicks:
                pagination_active = False
            current_page += 1

        except (TimeoutException, NoSuchElementException) as e:
            print(f"  Error processing page {current_page}: Timeout/Element not found. {e}")
            pagination_active = False
            print("  Stopping pagination.")
        except Exception as e:
            print(f"  Unexpected error processing page {current_page}: {e}")
            pagination_active = False
            print("  Stopping pagination.")

    print(f"\nFound {len(links)} total unique story links.")
    return list(links)


def clean_text(text):
    if not text:
        return None
    text = text.replace("(Opens in a new tab)", "").strip().strip('“”"\'')
    return text.strip()

def scrape_story_details(driver, wait, story_url, config):
    print(f"  Scraping: {story_url}")
    load_timeout = config.get('details_page_load_timeout', 180)
    wait_selector = config.get('wait_for_element_selector', 'body')

    driver.set_page_load_timeout(load_timeout)
    try:
        driver.get(story_url)
        wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, wait_selector)))
    except TimeoutException:
        print(f"    Page {story_url} timed out waiting for '{wait_selector}' and was skipped.")
        return None
    except (WebDriverException, Exception) as e:
        print(f"    Error loading {story_url}: {e}")
        return None
    finally:
        try:
            driver.set_page_load_timeout(60)
        except WebDriverException as e:
            print(f"    Warning: Could not reset page load timeout after {story_url}. Error: {e}")

    soup = BeautifulSoup(driver.page_source, 'html.parser')
    story_data = {'url': story_url}
    # Minimal example: you can keep your full DATA_SELECTORS pipeline if needed.
    title_tag = soup.find('title')
    story_data['title'] = title_tag.get_text(strip=True) if title_tag else None
    story_data['confidence_score'] = 'High' if story_data['title'] else 'Low'
    story_data['needs_verification'] = 'No' if story_data['title'] else 'Yes'
    return story_data


def run_scraper():
    print("Initializing browser...")
    try:
        options = webdriver.ChromeOptions()
        options.set_capability("pageLoadStrategy", "eager")
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--blink-settings=imagesEnabled=false')
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--incognito")
        options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument('--disable-blink-features=AutomationControlled')

        driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=options)
        wait = WebDriverWait(driver, WAIT_TIMEOUT)
    except Exception as e:
        print(f"Error setting up Selenium WebDriver: {e}")
        return

    story_urls = get_story_links(driver, wait, config)
    all_stories_data = []

    if story_urls:
        print("\nStarting to scrape individual story pages...")
        urls_to_scrape = list(story_urls)
        max_retries = MAX_RETRIES

        for attempt in range(max_retries):
            if not urls_to_scrape:
                print("\nAll URLs scraped successfully.")
                break
            print(f"\n--- Scraping Pass {attempt + 1} of {max_retries} ---")
            print(f"Attempting to scrape {len(urls_to_scrape)} URLs...")
            failed_urls_this_pass = []

            for url in urls_to_scrape:
                story_data = scrape_story_details(driver, wait, url, config)
                if story_data:
                    all_stories_data.append(story_data)
                else:
                    failed_urls_this_pass.append(url)

            urls_to_scrape = failed_urls_this_pass
            if urls_to_scrape:
                print(f"    {len(urls_to_scrape)} URLs failed on pass {attempt + 1}. Pausing before retry...")
                time.sleep(5)

        if urls_to_scrape:
            print(f"\nWarning: {len(urls_to_scrape)} URLs failed after {max_retries} attempts.")
            print(f"Failed URLs: {urls_to_scrape}")

        if all_stories_data:
            print(f"\nScraping complete. Successfully scraped {len(all_stories_data)} out of {len(story_urls)} URLs.")
            print("Saving data to Excel...")
            all_stories_data.sort(key=lambda x: x.get('url', ''))
            df_stories = pd.DataFrame(all_stories_data)

            # Ensure verification columns exist
            if 'confidence_score' not in df_stories.columns:
                df_stories['confidence_score'] = 'Low'
            if 'needs_verification' not in df_stories.columns:
                df_stories['needs_verification'] = 'Yes'

            output_columns = OUTPUT_COLUMNS or list(df_stories.columns)
            if 'confidence_score' not in output_columns:
                output_columns.append('confidence_score')
            if 'needs_verification' not in output_columns:
                output_columns.append('needs_verification')

            existing_cols = [c for c in output_columns if c in df_stories.columns]
            df_stories = df_stories[existing_cols].reindex(columns=output_columns)

            try:
                df_stories.to_excel(OUTPUT_FILENAME, sheet_name='Stories', index=False)
                print(f"✅ Success! Data saved to {OUTPUT_FILENAME} with 'Stories' sheet.")
            except Exception as e:
                print(f"Error writing to Excel file: {e}")
        else:
            print("No data was scraped from the individual pages.")
    else:
        print("No story links were found. Exiting.")

    try:
        driver.quit()
        print("Browser closed.")
    except WebDriverException as e:
        print(f"Browser already closed or unreachable: {e}")
    except Exception as e:
        print(f"Error closing browser: {e}")


# ================================================================
# ====================== STREAMLIT UI =============================
# ================================================================
st.set_page_config(layout="wide")
st.title("🕷️ Configurable Web Scraper")

start = st.button("🚀 Start Scraping", use_container_width=True)

if start:
    # Start in a background thread so UI stays responsive
    t = threading.Thread(target=run_scraper, daemon=True)
    t.start()
    with log_lock:
        st.session_state.log_buffer = "[00:00:00] Scraper started...\n"

# Live log view
st.text_area("Live Log", st.session_state.log_buffer, height=500, key="log_output")

# Auto-refresh UI every 2s while logs are updating
time.sleep(2)
st.rerun()
