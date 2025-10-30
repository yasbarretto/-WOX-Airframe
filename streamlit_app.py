import streamlit as st
import time
import pandas as pd
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import re
import json
import os
from io import BytesIO
import threading

# Selenium imports
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException, ElementClickInterceptedException
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options as ChromeOptions

# -------------------------------------------------------------
# --- Configuration and Session State Initialization ---
# -------------------------------------------------------------
CONFIG_FILE = 'config.json'
SECTION_KEYWORDS = {"challenge", "solution", "headquarters", "industry", "integrations", "share", "results", "the", "about", "at", "group", "financial"}

# Initialize Streamlit session state safely
defaults = {
    "is_running": False,
    "download_data": None,
    "download_filename": "",
    "log_buffer": "",
    "status_message": "Status: Idle. Load config to begin.",
}
for key, val in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = val

if 'config_text' not in st.session_state:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            st.session_state.config_text = f.read()
    else:
        st.session_state.config_text = json.dumps({"base_url": "https://www.example.com"}, indent=2)

# -------------------------------------------------------------
# --- Thread-Safe Logging Setup ---
# -------------------------------------------------------------
log_lock = threading.Lock()
log_messages = []

def log_callback(message: str):
    """Used by scraper threads to safely push log messages."""
    print(message)
    with log_lock:
        log_messages.append(message)

def flush_logs_to_session_state():
    """Pull logs from the thread buffer into Streamlit session_state safely."""
    global log_messages
    with log_lock:
        if log_messages:
            st.session_state.log_buffer += "\n".join(log_messages) + "\n"
            log_messages.clear()

# -------------------------------------------------------------
# --- Utility and Helper Functions ---
# -------------------------------------------------------------
def clean_text(text):
    if not text:
        return None
    text = text.replace("(Opens in a new tab)", "").strip().strip('“”"\'')
    return text.strip()

def get_text(element):
    return element.get_text(strip=True) if element else None

def get_first_p_before_heading_strict(element, soup):
    if not element:
        return None
    first_section = soup.find(['h3', 'strong'], string=re.compile(r'challenge|solution|results', re.I))
    is_before = True
    if first_section:
        head_parent = first_section.find_parent(['div','p'])
        if head_parent and not (element.compare_position(head_parent) & 4):
            is_before = False
    if is_before:
        first_p = element.find('p')
        if first_p:
            p_text = first_p.get_text(strip=True)
            if len(p_text) > 20 and p_text.lower() not in SECTION_KEYWORDS:
                return p_text
    return None

def get_next_p_after_title_strict(element, soup):
    if not element:
        return None
    first_section = soup.find(['h3', 'strong'], string=re.compile(r'challenge|solution|results', re.I))
    potential_p = element.find_next_sibling('p')
    if not potential_p and element.find_next_sibling('div'):
        potential_p = element.find_next_sibling('div').find('p')
    if potential_p:
        is_before = True
        if first_section:
            head_parent = first_section.find_parent(['div','p'])
            if head_parent and not (potential_p.compare_position(head_parent) & 4):
                is_before = False
        if is_before:
            p_text = potential_p.get_text(strip=True)
            if len(p_text) > 50 and p_text.lower() not in SECTION_KEYWORDS and not re.match(r'^[\w\s\'.-]+[,–—\-]', p_text):
                return p_text
    return None

def get_text_after_key_h3_rte(element):
    if element:
        value_div = element.find_next_sibling('div', class_='rte')
        return value_div.get_text(strip=True) if value_div else None
    return None

def get_text_after_strong_sep_p(element):
    if element:
        key_p = element.find_parent('p')
        if key_p and element.get_text(strip=True) == key_p.get_text(strip=True):
            value_p = key_p.find_next_sibling('p')
            return value_p.get_text(strip=True) if value_p else None
    return None

def get_text_after_strong_same_p(element):
    if element:
        full_p = element.find_parent('p')
        if full_p:
            full_text = full_p.get_text(strip=True)
            key_text = element.get_text(strip=True)
            value_text = full_text.replace(key_text, '', 1).strip()
            return value_text if value_text else None
    return None

def get_text_after_strong_spacey_rte(element):
    if element:
        parent_div = element.find_parent('div', class_='space-y-8')
        if parent_div:
            value_div = element.find_next_sibling('div', class_='rte')
            return value_div.get_text(strip=True) if value_div else None
    return None

EXTRACTION_METHODS = {
    "text": get_text,
    "first_p_before_heading_strict": get_first_p_before_heading_strict,
    "next_p_after_title_strict": get_next_p_after_title_strict,
    "text_after_key_h3_rte": get_text_after_key_h3_rte,
    "text_after_strong_in_separate_p": get_text_after_strong_sep_p,
    "parent_text_after_strong": get_text_after_strong_same_p,
    "text_after_strong_spacey_rte": get_text_after_strong_spacey_rte,
}

# -------------------------------------------------------------
# --- Scraper Core Logic ---
# -------------------------------------------------------------
def get_story_links(driver, wait, config, log_callback):
    base_url = config['base_url']
    max_pages_or_clicks = config['max_pages_to_scrape']
    pagination_type = config['pagination_type']
    next_page_selector_template = config['next_page_button_selector']
    list_selector = config['story_card_list_selector']
    link_selector = config['story_card_link_selector']

    log_callback(f"Navigating to {base_url} to find links...")
    driver.get(base_url)
    time.sleep(3)
    links = set()
    first_link_full_selector = f"{list_selector} {link_selector}:first-of-type"

    try:
        cookie_wait = WebDriverWait(driver, 10)
        cookie_button = cookie_wait.until(EC.element_to_be_clickable((By.XPATH,
            "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'accept all') or contains(@id, 'accept') or contains(@class, 'accept')] | //a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'accept all')]")))
        log_callback("  Found and clicked cookie accept button.")
        driver.execute_script("arguments[0].click();", cookie_button)
        time.sleep(2)
    except Exception:
        log_callback("  No cookie banner found. Continuing...")

    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, list_selector)))
        log_callback(f"  Container ('{list_selector}') found.")
    except TimeoutException:
        log_callback(f"Error: Initial container not found.")
        return []

    current_page = 1
    pagination_active = True
    last_first_href = ""
    while pagination_active and current_page <= max_pages_or_clicks:
        log_callback(f"--- Processing Page {current_page} ---")
        current_first_href = None
        try:
            first_link_element = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, first_link_full_selector)))
            current_first_href = first_link_element.get_attribute('data-href') or first_link_element.get_attribute('href')
            time.sleep(2)
            if current_page > 1 and current_first_href == last_first_href:
                log_callback(f"    Content unchanged. Stopping pagination.")
                break
            last_first_href = current_first_href

            link_elements = driver.find_elements(By.CSS_SELECTOR, f"{list_selector} {link_selector}")
            for element in link_elements:
                href = element.get_attribute('data-href') or element.get_attribute('href')
                if href:
                    full_url = urljoin(base_url, href)
                    if full_url != base_url and href != '#':
                        links.add(full_url)

            log_callback(f"  Found {len(links)} total so far.")

            if current_page < max_pages_or_clicks and next_page_selector_template:
                if pagination_type == 'click_load_more':
                    log_callback("  Trying 'Load More' button...")
                    try:
                        load_more_button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, next_page_selector_template)))
                        driver.execute_script("arguments[0].click();", load_more_button)
                        time.sleep(3)
                    except Exception:
                        log_callback("  No more pages or failed to click.")
                        pagination_active = False
                        break
                else:
                    pagination_active = False
            else:
                pagination_active = False

            current_page += 1
        except Exception as e:
            log_callback(f"Error on page {current_page}: {e}")
            pagination_active = False
    return list(links)

def scrape_story_details(driver, wait, story_url, config, log_callback):
    log_callback(f"  Scraping: {story_url}")
    load_timeout = config.get('details_page_load_timeout', 180)
    wait_selector = config.get('wait_for_element_selector', 'body')
    data_selectors = config.get('data_selectors', {})
    conf_thresholds = config.get('confidence_thresholds', {"high": 7, "medium": 4})
    driver.set_page_load_timeout(load_timeout)

    try:
        driver.get(story_url)
        wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, wait_selector)))
    except Exception as e:
        log_callback(f"    Failed to load {story_url}: {e}")
        return None

    soup = BeautifulSoup(driver.page_source, 'html.parser')
    story_data = {'url': story_url}
    confidence_points = 0

    for field, selectors in data_selectors.items():
        field_value = None
        if not isinstance(selectors, list):
            continue
        for selector_config in selectors:
            selector = selector_config.get('selector')
            method_name = selector_config.get('method')
            confidence = selector_config.get('confidence', 0)
            min_length = selector_config.get('min_length', 0)
            if not selector or not method_name:
                continue
            try:
                element = soup.select_one(selector)
                if not element:
                    continue
                extraction_func = EXTRACTION_METHODS.get(method_name)
                if extraction_func:
                    value = extraction_func(element, soup) if 'strict' in method_name else extraction_func(element)
                    cleaned = clean_text(value)
                    if cleaned and len(cleaned) >= min_length:
                        field_value = cleaned
                        confidence_points += confidence
                        break
            except Exception as e:
                log_callback(f"    Error extracting {field}: {e}")
        story_data[field] = field_value

    if confidence_points >= conf_thresholds['high']:
        story_data['confidence_score'] = 'High'
        story_data['needs_verification'] = 'No'
    elif confidence_points >= conf_thresholds['medium']:
        story_data['confidence_score'] = 'Medium'
        story_data['needs_verification'] = 'Yes'
    else:
        story_data['confidence_score'] = 'Low'
        story_data['needs_verification'] = 'Yes'
    return story_data

def run_scraper_main(config, is_headless, log_callback, status_callback, finish_callback):
    driver = None
    try:
        log_callback("Initializing browser...")
        status_callback("Initializing browser...")

        options = ChromeOptions()
        options.set_capability("pageLoadStrategy", "eager")
        if is_headless:
            options.add_argument('--headless')
            options.add_argument('--disable-gpu')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument("--window-size=1920,1080")
        options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
        options.add_experimental_option('useAutomationExtension', False)
        driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=options)
        wait = WebDriverWait(driver, config.get('main_wait_timeout', 60))
    except Exception as e:
        log_callback(f"WebDriver Error: {e}")
        finish_callback(False, f"WebDriver Error: {e}")
        return

    try:
        status_callback("Finding links...")
        story_urls = get_story_links(driver, wait, config, log_callback)
        if not story_urls:
            finish_callback(False, "No story links found.")
            return
        log_callback(f"Found {len(story_urls)} story URLs.")

        results = []
        for idx, url in enumerate(story_urls, start=1):
            status_callback(f"Scraping {idx}/{len(story_urls)}")
            data = scrape_story_details(driver, wait, url, config, log_callback)
            if data:
                results.append(data)

        if not results:
            finish_callback(False, "No data scraped.")
            return

        df = pd.DataFrame(results)
        output_buffer = BytesIO()
        with pd.ExcelWriter(output_buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Stories')
        output_buffer.seek(0)
        finish_callback(True, f"Scraped {len(results)} stories.", output_buffer, "scraped_data.xlsx")
    except Exception as e:
        log_callback(f"Unexpected error: {e}")
        finish_callback(False, f"Unexpected error: {e}")
    finally:
        try:
            driver.quit()
        except:
            pass

# -------------------------------------------------------------
# --- Streamlit Front-End ---
# -------------------------------------------------------------
st.set_page_config(layout="wide")
st.title("🤖 Configurable Web Scraper")

def status_callback(message):
    st.session_state.status_message = f"Status: {message}"

def finish_callback(success, message, data_buffer=None, filename=None):
    if success:
        st.session_state.status_message = f"Success: {message}"
        st.session_state.download_data = data_buffer
        st.session_state.download_filename = filename
    else:
        st.session_state.status_message = f"Error: {message}"
    st.session_state.is_running = False

# Sidebar Config Editor
st.sidebar.title("Configuration")
try:
    cfg = json.loads(st.session_state.config_text)
    st.sidebar.info(f"Current Config: **{cfg.get('base_url', 'N/A')}**")
except Exception as e:
    st.sidebar.error(f"Invalid JSON: {e}")

with st.sidebar.expander("Edit Configuration File", expanded=False):
    config_editor_text = st.text_area("Config JSON", st.session_state.config_text, height=400, key="config_editor_area")
    if st.button("Save Configuration"):
        try:
            json.loads(config_editor_text)
            with open(CONFIG_FILE, 'w') as f:
                f.write(config_editor_text)
            st.session_state.config_text = config_editor_text
            st.sidebar.success("Configuration saved successfully!")
            time.sleep(1)
            st.rerun()
        except json.JSONDecodeError as e:
            st.sidebar.error(f"Invalid JSON: {e}")
        except Exception as e:
            st.sidebar.error(f"Error saving file: {e}")

# Layout
col1, col2 = st.columns([1, 2])
with col1:
    st.header("Controls")
    is_headless = st.checkbox("Run in Headless Mode", value=True)
    start_button = st.button("🚀 Start Scraping", disabled=st.session_state.is_running, use_container_width=True)
    status_placeholder = st.empty()
    msg = st.session_state.status_message
    if "Success" in msg:
        status_placeholder.success(msg)
    elif "Error" in msg:
        status_placeholder.error(msg)
    else:
        status_placeholder.info(msg)
    if st.session_state.download_data:
        st.download_button(
            label=f"⬇️ Download {st.session_state.download_filename}",
            data=st.session_state.download_data,
            file_name=st.session_state.download_filename,
            mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            use_container_width=True
        )

with col2:
    st.header("Live Log")
    st.text_area("Log Output", st.session_state.log_buffer, height=400, key="log_output_main")

# Start Scraper Thread
if start_button and not st.session_state.is_running:
    st.session_state.is_running = True
    st.session_state.download_data = None
    st.session_state.download_filename = ""
    st.session_state.log_buffer = ""
    st.session_state.status_message = "Status: Starting scraper thread..."

    try:
        cfg = json.loads(st.session_state.config_text)
    except Exception as e:
        st.error(f"Cannot start: Invalid JSON. {e}")
        st.session_state.is_running = False
        st.stop()

    scraper_thread = threading.Thread(
        target=run_scraper_main,
        args=(cfg, is_headless, log_callback, status_callback, finish_callback),
        daemon=True
    )
    scraper_thread.start()
    st.rerun()

# Auto-refresh while running
if st.session_state.is_running:
    flush_logs_to_session_state()
    time.sleep(2)
    st.rerun()
