import streamlit as st
import time
import threading
import pandas as pd
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import re
import os
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
global_log_buffer = ""
_builtin_print = print

def log_callback(message):
    global global_log_buffer
    timestamp = time.strftime("[%H:%M:%S]")
    msg = f"{timestamp} {message}"
    _builtin_print(msg)
    with log_lock:
        global_log_buffer += msg + "\n"

print = log_callback

# ===============================
# Constants and Config
# ===============================
BASE_URL = "https://www.seismic.com/customer-stories/"
MAX_RETRIES = 3
SECTION_KEYWORDS = {"challenge", "solution", "headquarters", "industry", "integrations", "share", "results"}

# ===============================
# Scraper logic (full Seismic version)
# ===============================
def get_story_links(driver, wait, main_url):
    print(f"Navigating to {main_url} to find links...")
    driver.get(main_url)
    links = set()
    last_first_href = ""

    try:
        cookie_wait = WebDriverWait(driver, 5)
        cookie_button = cookie_wait.until(EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler")))
        print("  Found and clicked cookie accept button.")
        cookie_button.click()
        time.sleep(1)
    except Exception:
        print("  No cookie banner found, continuing...")

    card_list_selector = "ul[class*='grid-cols-1']"
    first_card_link_selector = f"{card_list_selector} li:first-child a"

    for page_num in range(1, 7):
        print(f"  Scraping page {page_num}...")
        try:
            if page_num > 1:
                page_button_selector = f'a[data-page="{page_num}"]'
                page_button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, page_button_selector)))
                driver.execute_script("arguments[0].click();", page_button)
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, f'a[data-page="{page_num}"][data-current="true"]')))
                wait.until_not(EC.text_to_be_present_in_element_attribute(
                    (By.CSS_SELECTOR, first_card_link_selector), "href", last_first_href
                ))

            wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, first_card_link_selector)))
            last_first_href = driver.find_element(By.CSS_SELECTOR, first_card_link_selector).get_attribute('href')

        except Exception as e:
            print(f"    Timed out or content not found on page {page_num}. Skipping.")
            continue

        soup = BeautifulSoup(driver.page_source, 'html.parser')
        story_list_ul = soup.find('ul', class_=lambda c: c and 'grid-cols-1' in c)

        found_on_page = 0
        if story_list_ul:
            for a_tag in story_list_ul.find_all('a', href=True):
                href = a_tag['href']
                if "seismic.com/customer-stories/" in href and len(href.split('/')) > 5:
                    if href not in links:
                        links.add(href)
                        found_on_page += 1
        print(f"    Found {found_on_page} new links on this page.")

    print(f"\nFound {len(links)} total unique story links.")
    return list(links)


def clean_text(text):
    if not text:
        return None
    return text.replace("(Opens in a new tab)", "").strip()


def scrape_story_details(driver, wait, story_url):
    print(f"  Scraping: {story_url}")
    driver.set_page_load_timeout(180)
    try:
        driver.get(story_url)
        h1_locator = (By.TAG_NAME, 'h1')
        title_div_locator = (By.CSS_SELECTOR, "div.rte[data-component*='richtextwrapper'][class*='text-h3']")
        h2_locator = (By.TAG_NAME, 'h2')
        details_locator = (By.CSS_SELECTOR, "div[class*='lg:col-span-5'][class*='self-start']")
        wait.until(EC.any_of(
            EC.visibility_of_element_located(h1_locator),
            EC.visibility_of_element_located(title_div_locator),
            EC.visibility_of_element_located(h2_locator),
            EC.visibility_of_element_located(details_locator)
        ))
    except Exception as e:
        print(f"    Timeout or error loading {story_url}: {e}")
        return None
    finally:
        driver.set_page_load_timeout(60)

    soup = BeautifulSoup(driver.page_source, 'html.parser')
    story_data = {'url': story_url}
    confidence_points = 0

    # Company name
    try:
        name = story_url.split('/customer-stories/')[1].strip('/')
        story_data['company_name'] = name.replace('-', ' ').title()
    except Exception:
        story_data['company_name'] = None

    # Title
    title_element = None
    try:
        page_title = None
        h1_tag = soup.find('h1')
        if h1_tag:
            t = h1_tag.get_text(strip=True)
            if t.lower() not in SECTION_KEYWORDS:
                page_title = t
                title_element = h1_tag
                confidence_points += 2
        if not page_title:
            div_tag = soup.find('div', class_=lambda c: c and 'rte' in c and 'text-h3' in c)
            if div_tag:
                t = div_tag.get_text(strip=True)
                if t and t.lower() not in SECTION_KEYWORDS:
                    page_title = t
                    title_element = div_tag
                    confidence_points += 2
        story_data['title'] = page_title
    except Exception as e:
        print(f"    Could not parse title: {e}")
        story_data['title'] = None

    # Description
    try:
        desc = None
        desc_div = soup.find('div', class_=lambda c: c and 'lg:col-span-7' in c and 'xl:ml-auto' in c)
        if desc_div:
            p_tags = desc_div.find_all('p')
            if p_tags:
                desc = " ".join(p.get_text(strip=True) for p in p_tags)
                confidence_points += 2
        elif title_element:
            p_tag = title_element.find_next_sibling('p')
            if p_tag and len(p_tag.get_text(strip=True)) > 50:
                desc = p_tag.get_text(strip=True)
                confidence_points += 1
        story_data['description'] = clean_text(desc)
    except Exception as e:
        print(f"    Could not parse description: {e}")
        story_data['description'] = None

    # Challenge, Solution, HQ, Industry, Integrations
    keys_to_find = ["Challenge", "Solution", "HEADQUARTERS", "INDUSTRY", "INTEGRATIONS"]
    found_count = 0
    for key in keys_to_find:
        key_lower = key.lower()
        value = None
        key_tag_h3 = soup.find('h3', string=re.compile(rf'^\s*{key}\s*$', re.I))
        if key_tag_h3:
            val_div = key_tag_h3.find_next_sibling('div', class_='rte')
            if val_div:
                value = val_div.get_text(strip=True)
        if not value:
            key_tag_strong = soup.find('strong', string=re.compile(rf'^\s*{key}\s*$', re.I))
            if key_tag_strong:
                val_p = key_tag_strong.find_parent('p').find_next_sibling('p')
                if val_p:
                    value = val_p.get_text(strip=True)
        story_data[key_lower] = clean_text(value)
        if value:
            found_count += 1
    confidence_points += found_count

    # Confidence summary
    if confidence_points >= 7:
        story_data['confidence_score'] = 'High'
        story_data['needs_verification'] = 'No'
    elif confidence_points >= 4:
        story_data['confidence_score'] = 'Medium'
        story_data['needs_verification'] = 'Yes'
    else:
        story_data['confidence_score'] = 'Low'
        story_data['needs_verification'] = 'Yes'

    return story_data


# ===============================
# Run scraper (hybrid driver + logging)
# ===============================
def run_scraper():
    print("🚀 Scraper starting...")
    driver = None
    try:
        options = webdriver.ChromeOptions()
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--disable-gpu')
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--incognito")
        options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument('--disable-blink-features=AutomationControlled')

        if os.path.exists("/usr/bin/chromium") and os.path.exists("/usr/bin/chromedriver"):
            print("Detected system Chromium — using /usr/bin/chromedriver")
            options.binary_location = "/usr/bin/chromium"
            service = ChromeService(executable_path="/usr/bin/chromedriver")
            driver = webdriver.Chrome(service=service, options=options)
        else:
            driver_path = ChromeDriverManager().install()
            print(f"Using webdriver_manager driver: {driver_path}")
            service = ChromeService(executable_path=driver_path)
            driver = webdriver.Chrome(service=service, options=options)

        wait = WebDriverWait(driver, 180)
        print("🧠 Chrome ready.")

        story_urls = get_story_links(driver, wait, BASE_URL)
        all_stories_data = []

        if story_urls:
            print(f"\nStarting to scrape {len(story_urls)} pages...")
            urls_to_scrape = list(story_urls)
            for attempt in range(MAX_RETRIES):
                if not urls_to_scrape:
                    break
                print(f"\n--- Scraping Pass {attempt + 1} of {MAX_RETRIES} ---")
                failed = []
                for url in urls_to_scrape:
                    data = scrape_story_details(driver, wait, url)
                    if data:
                        all_stories_data.append(data)
                    else:
                        failed.append(url)
                urls_to_scrape = failed
                if failed:
                    print(f"Retrying {len(failed)} failed URLs...")

            if all_stories_data:
                df = pd.DataFrame(all_stories_data)
                cols = ['url', 'company_name', 'title', 'description', 'challenge', 'solution', 'headquarters', 'industry', 'integrations', 'confidence_score', 'needs_verification']
                df = df.reindex(columns=cols)
                output_file = "seismic_customer_stories_STREAMLIT.xlsx"
                df.to_excel(output_file, index=False)
                print(f"✅ Saved to {output_file}")
            else:
                print("No data scraped.")
        else:
            print("No story links found.")

    except Exception as e:
        print(f"❌ Fatal error: {e}")
    finally:
        if driver:
            driver.quit()
            print("Browser closed.")


# ===============================
# Streamlit UI (non-blocking)
# ===============================
st.set_page_config(layout="wide")
st.title("🕷️ Seismic Customer Stories Scraper")

if "log_buffer" not in st.session_state:
    st.session_state.log_buffer = ""
if "scraper_running" not in st.session_state:
    st.session_state.scraper_running = False
if "scraper_done" not in st.session_state:
    st.session_state.scraper_done = False

# --- Start scraper ---
if st.button("🚀 Start Scraping", use_container_width=True, disabled=st.session_state.scraper_running):
    st.session_state.log_buffer = "[00:00:00] 🚀 Scraper starting...\n"
    st.session_state.scraper_running = True
    st.session_state.scraper_done = False
    t = threading.Thread(target=run_scraper, daemon=True)
    t.start()
    st.rerun()

# --- Update logs ---
with log_lock:
    if global_log_buffer:
        st.session_state.log_buffer += global_log_buffer
        global_log_buffer = ""

st.text_area("Live Log", st.session_state.log_buffer, height=500, key="live_log_area")

# --- Check scraper state ---
threads_alive = any(thread.is_alive() for thread in threading.enumerate() if thread.name != "MainThread")

if st.session_state.scraper_running and not threads_alive:
    st.session_state.scraper_running = False
    st.session_state.scraper_done = True
    st.rerun()  # trigger one final refresh

# --- After scraper finishes ---
output_file = "seismic_customer_stories_STREAMLIT.xlsx"

if st.session_state.scraper_done:
    if os.path.exists(output_file):
        with open(output_file, "rb") as f:
            st.success("✅ Scraping complete! File saved successfully.")
            st.download_button(
                "📥 Download Excel Results",
                f,
                file_name=output_file,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
    else:
        st.warning("⚠️ Scraper finished but output file not found. Check logs above.")
else:
    # Auto-refresh every 2s if still scraping
    if st.session_state.scraper_running:
        time.sleep(2)
        st.rerun()

