import streamlit as st
import time
import threading
import pandas as pd
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import re
import json
import os

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
global_log_buffer = ""   # global variable used by threads
_builtin_print = print   # keep reference to real print()

def log_callback(message):
    """Thread-safe logger that works inside and outside Streamlit."""
    global global_log_buffer
    timestamp = time.strftime("[%H:%M:%S]")
    msg = f"{timestamp} {message}"
    _builtin_print(msg)
    with log_lock:
        global_log_buffer += msg + "\n"

# Redirect print() calls to log_callback
print = log_callback


# ===============================
# CONFIGURATION LOADING
# ===============================
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
WAIT_TIMEOUT = config.get('main_wait_timeout', 180)
MAX_RETRIES = config.get('max_retries', 3)
OUTPUT_FILENAME = config.get('output_filename', 'scraped_data.xlsx')
OUTPUT_COLUMNS = config.get('output_columns', [])
if not BASE_URL:
    print("Error: 'base_url' must be defined in the configuration file.")
    raise SystemExit

SECTION_KEYWORDS = {"challenge", "solution", "headquarters", "industry", "integrations", "share", "results", "the", "about", "at", "group", "financial"}


# ===============================
# SCRAPER LOGIC
# ===============================
def get_story_links(driver, wait, config):
    base_url = config['base_url']
    print(f"Navigating to {base_url} to find links...")
    driver.get(base_url)
    time.sleep(3)
    links = set()

    try:
        list_selector = config.get('story_card_list_selector', 'body')
        link_selector = config.get('story_card_link_selector', 'a')
        link_elements = driver.find_elements(By.CSS_SELECTOR, f"{list_selector} {link_selector}")
        print(f"  Found {len(link_elements)} potential link elements.")
        for el in link_elements:
            href = el.get_attribute('href') or el.get_attribute('data-href')
            if href and href != '#':
                links.add(urljoin(base_url, href))
    except Exception as e:
        print(f"Error finding story links: {e}")

    print(f"✅ Found {len(links)} unique links.")
    return list(links)


def clean_text(text):
    if not text:
        return None
    return text.replace("(Opens in a new tab)", "").strip().strip('“”"\'').strip()


def scrape_story_details(driver, wait, story_url, config):
    print(f"  Scraping: {story_url}")
    try:
        driver.get(story_url)
        wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, config.get('wait_for_element_selector', 'body'))))
    except Exception as e:
        print(f"    Error loading {story_url}: {e}")
        return None

    soup = BeautifulSoup(driver.page_source, 'html.parser')
    story_data = {'url': story_url}
    title_tag = soup.find('title')
    story_data['title'] = clean_text(title_tag.get_text()) if title_tag else None
    story_data['confidence_score'] = 'High' if story_data['title'] else 'Low'
    story_data['needs_verification'] = 'No' if story_data['title'] else 'Yes'
    return story_data


# ===============================
# RUN SCRAPER MAIN
# ===============================
def run_scraper():
    print("🚀 Scraper starting...")
    print("Step 1: Initializing Chrome driver ...")
    driver = None
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

        # --- Smart Chrome detection ---
        if os.path.exists("/usr/bin/chromium") and os.path.exists("/usr/bin/chromedriver"):
            print("Detected system Chromium — using /usr/bin/chromedriver")
            options.binary_location = "/usr/bin/chromium"
            service = ChromeService(executable_path="/usr/bin/chromedriver")
            driver = webdriver.Chrome(service=service, options=options)
        else:
            try:
                driver_path = ChromeDriverManager().install()
                print(f"Using webdriver_manager installed driver: {driver_path}")
                service = ChromeService(executable_path=driver_path)
                driver = webdriver.Chrome(service=service, options=options)
            except Exception as e:
                print(f"⚠️ ChromeDriverManager failed: {e}")
                import undetected_chromedriver as uc
                driver = uc.Chrome(options=options, headless=True)
                print("✅ Using undetected_chromedriver fallback.")

        if not driver:
            print("❌ Failed to initialize any Chrome driver.")
            return

        wait = WebDriverWait(driver, WAIT_TIMEOUT)
        print("🧠 Thread launched successfully.")

        # --- Main scraping ---
        story_urls = get_story_links(driver, wait, config)
        all_stories_data = []

        if story_urls:
            print(f"Starting scrape of {len(story_urls)} pages...")
            for url in story_urls:
                data = scrape_story_details(driver, wait, url, config)
                if data:
                    all_stories_data.append(data)
            print(f"✅ Scraped {len(all_stories_data)} stories.")

            if all_stories_data:
                df = pd.DataFrame(all_stories_data)
                df.to_excel(OUTPUT_FILENAME, index=False)
                print(f"💾 Saved to {OUTPUT_FILENAME}")
            else:
                print("No stories scraped successfully.")
        else:
            print("No story links found.")

    except Exception as e:
        print(f"❌ Fatal error initializing WebDriver: {e}")
    finally:
        if driver:
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

# Initialize session state
if "log_buffer" not in st.session_state:
    st.session_state.log_buffer = ""

# Start button
start = st.button("🚀 Start Scraping", use_container_width=True)

if start:
    with log_lock:
        st.session_state.log_buffer = "[00:00:00] 🚀 Scraper starting...\n"
    t = threading.Thread(target=run_scraper, daemon=True)
    t.start()

# Sync logs from global buffer
with log_lock:
    if global_log_buffer:
        st.session_state.log_buffer += global_log_buffer
        global_log_buffer = ""

# Display log
st.text_area("Live Log", st.session_state.log_buffer, height=500, key="log_output")

# Download button (if Excel exists)
if os.path.exists(OUTPUT_FILENAME):
    with open(OUTPUT_FILENAME, "rb") as f:
        st.download_button(
            "📥 Download Results",
            f,
            file_name=OUTPUT_FILENAME,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )

# Auto-refresh
time.sleep(2)
st.rerun()
