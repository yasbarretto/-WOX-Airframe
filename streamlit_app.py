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

# --- Thread-safe logging setup ---
log_lock = threading.Lock()
if "log_buffer" not in st.session_state:
    st.session_state.log_buffer = ""

# Keep original print
_builtin_print = print

def log_callback(message):
    """Send logs to Streamlit UI and terminal."""
    timestamp = time.strftime("[%H:%M:%S]")
    msg = f"{timestamp} {message}"
    _builtin_print(msg)  # use original print to console
    with log_lock:
        st.session_state.log_buffer += msg + "\n"

# Redirect all prints to the Streamlit-safe version
print = log_callback


# ================================================================
# ===============  ORIGINAL SCRAPER CODE BELOW  ==================
# ================================================================
CONFIG_FILE = 'config.json'
try:
    with open(CONFIG_FILE, 'r') as f:
        config = json.load(f)
    print(f"Loaded configuration from {CONFIG_FILE}")
except FileNotFoundError:
    print(f"Error: Configuration file '{CONFIG_FILE}' not found.")
    exit()
except json.JSONDecodeError as e:
    print(f"Error: Could not parse configuration file '{CONFIG_FILE}'. Invalid JSON: {e}")
    exit()
except Exception as e:
    print(f"An unexpected error occurred loading the configuration: {e}")
    exit()

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
    exit()

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

    try:
        cookie_wait = WebDriverWait(driver, 10)
        cookie_button = cookie_wait.until(EC.element_to_be_clickable(
            (By.XPATH, "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'accept all')]")))
        print("Found and clicked cookie accept button.")
        driver.execute_script("arguments[0].click();", cookie_button)
        time.sleep(2)
    except Exception:
        print("No cookie banner found or timed out. Continuing...")

    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, list_selector)))
        print(f"Initial container ('{list_selector}') found.")
    except TimeoutException:
        print(f"Error: Initial container/link not found/visible.")
        return []

    link_elements = driver.find_elements(By.CSS_SELECTOR, f"{list_selector} {link_selector}")
    for element in link_elements:
        href = element.get_attribute("href")
        if href:
            links.add(urljoin(base_url, href))

    print(f"Found {len(links)} total unique story links.")
    return list(links)


def scrape_story_details(driver, wait, story_url, config):
    print(f"Scraping: {story_url}")
    try:
        driver.set_page_load_timeout(config.get('details_page_load_timeout', 180))
        driver.get(story_url)
        wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, config.get('wait_for_element_selector', 'body'))))
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        title = soup.title.string.strip() if soup.title else "(No title)"
        return {"url": story_url, "title": title, "confidence_score": "High", "needs_verification": "No"}
    except Exception as e:
        print(f"Error scraping {story_url}: {e}")
        return None


def main():
    print("Initializing browser...")
    try:
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")

        driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=options)
        wait = WebDriverWait(driver, config.get('main_wait_timeout', 180))
    except Exception as e:
        print(f"Error setting up Selenium WebDriver: {e}")
        return

    story_urls = get_story_links(driver, wait, config)
    all_stories_data = []

    if story_urls:
        print("Starting to scrape individual story pages...")
        for url in story_urls:
            data = scrape_story_details(driver, wait, url, config)
            if data:
                all_stories_data.append(data)

        if all_stories_data:
            df = pd.DataFrame(all_stories_data)
            df.to_excel(config.get('output_filename', 'scraped_data.xlsx'), index=False)
            print("✅ Scraping complete and saved to Excel.")
        else:
            print("⚠️ No data scraped.")
    else:
        print("⚠️ No story links found.")

    try:
        driver.quit()
        print("Browser closed.")
    except Exception as e:
        print(f"Error closing browser: {e}")


# ================================================================
# ====================== STREAMLIT UI =============================
# ================================================================
st.set_page_config(layout="wide")
st.title("🕷️ Configurable Web Scraper")

if st.button("🚀 Start Scraping", use_container_width=True):
    thread = threading.Thread(target=main, daemon=True)
    thread.start()
    st.session_state.log_buffer = "[00:00:00] Scraper started...\n"

st.text_area("Live Log", st.session_state.log_buffer, height=500, key="log_output")

# Auto-refresh every 2 seconds
time.sleep(2)
st.rerun()
