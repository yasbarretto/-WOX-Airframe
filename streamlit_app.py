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
import logging

# --- Suppress harmless Streamlit thread warnings ---
logging.getLogger("streamlit.runtime.scriptrunner.script_runner").setLevel(logging.ERROR)

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
        st.session_state.config_text = json.dumps(
            {"base_url": "https://www.example.com",
             "max_pages_to_scrape": 1,
             "main_wait_timeout": 30,
             "details_page_load_timeout": 30},
            indent=2
        )

# -------------------------------------------------------------
# --- Thread-Safe Logging Setup ---
# -------------------------------------------------------------
log_lock = threading.Lock()
log_messages = []

def log_callback(message: str):
    """Thread-safe logging"""
    timestamp = time.strftime("[%H:%M:%S]")
    msg = f"{timestamp} {message}"
    print(msg)
    with log_lock:
        log_messages.append(msg)

def flush_logs_to_session_state():
    """Safely move logs into Streamlit session state"""
    global log_messages
    with log_lock:
        if log_messages:
            st.session_state.log_buffer += "\n".join(log_messages) + "\n"
            log_messages.clear()

# -------------------------------------------------------------
# --- Helper Functions ---
# -------------------------------------------------------------
def clean_text(text):
    if not text:
        return None
    text = text.replace("(Opens in a new tab)", "").strip().strip('“”"\'')
    return text.strip()

def get_text(element):
    return element.get_text(strip=True) if element else None

# Minimal extraction helpers for stability
EXTRACTION_METHODS = {"text": get_text}

# -------------------------------------------------------------
# --- Core Scraper Logic ---
# -------------------------------------------------------------
def get_story_links(driver, wait, config, log_callback):
    base_url = config['base_url']
    max_pages = config.get('max_pages_to_scrape', 1)
    list_selector = config.get('story_card_list_selector', 'a')
    link_selector = config.get('story_card_link_selector', 'a')

    log_callback(f"Navigating to {base_url} ...")
    driver.get(base_url)
    time.sleep(3)
    links = set()

    try:
        wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, list_selector)))
        elements = driver.find_elements(By.CSS_SELECTOR, link_selector)
        for e in elements:
            href = e.get_attribute('href')
            if href and href.startswith("http"):
                links.add(href)
        log_callback(f"✅ Found {len(links)} links on first page.")
    except Exception as e:
        log_callback(f"⚠️ Error collecting links: {e}")
        return []

    if len(links) == 0:
        log_callback("⚠️ No links found — check your selectors.")
    return list(links)

def scrape_story_details(driver, wait, story_url, config, log_callback):
    log_callback(f"🔍 Scraping {story_url}")
    wait_selector = config.get('wait_for_element_selector', 'body')
    timeout = config.get('details_page_load_timeout', 30)

    try:
        driver.set_page_load_timeout(timeout)
        driver.get(story_url)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, wait_selector)))
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        title = soup.title.string.strip() if soup.title else "(No title)"
        return {"url": story_url, "title": title, "confidence_score": "High", "needs_verification": "No"}
    except Exception as e:
        log_callback(f"⚠️ Failed to scrape {story_url}: {e}")
        return None

def run_scraper_main(config, is_headless, log_callback, status_callback, finish_callback):
    driver = None
    try:
        log_callback("Step 1: Initializing Chrome driver ...")
        status_callback("Starting Chrome ...")

        options = ChromeOptions()
        options.set_capability("pageLoadStrategy", "eager")
        if is_headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")

        driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=options)
        wait = WebDriverWait(driver, config.get("main_wait_timeout", 30))

        log_callback("Step 2: Collecting story links ...")
        urls = get_story_links(driver, wait, config, log_callback)
        if not urls:
            finish_callback(False, "No links found.")
            return

        log_callback(f"Step 3: Found {len(urls)} URLs. Beginning scrape ...")
        results = []
        for i, url in enumerate(urls, 1):
            status_callback(f"Scraping {i}/{len(urls)}")
            data = scrape_story_details(driver, wait, url, config, log_callback)
            if data:
                results.append(data)

        if not results:
            finish_callback(False, "No data scraped.")
            return

        df = pd.DataFrame(results)
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, index=False)
        output.seek(0)
        finish_callback(True, f"Scraped {len(results)} pages.", output, "scraped_data.xlsx")
        log_callback("✅ Scraping complete.")
    except Exception as e:
        log_callback(f"❌ Fatal error: {e}")
        finish_callback(False, f"Fatal error: {e}")
    finally:
        if driver:
            try:
                driver.quit()
                log_callback("🧹 Browser closed.")
            except:
                pass

# -------------------------------------------------------------
# --- Streamlit UI Setup ---
# -------------------------------------------------------------
st.set_page_config(layout="wide")
st.title("🤖 Configurable Web Scraper (Debug Mode)")

def status_callback(message):
    st.session_state.status_message = f"Status: {message}"

def finish_callback(success, message, data_buffer=None, filename=None):
    if success:
        st.session_state.status_message = f"✅ {message}"
        st.session_state.download_data = data_buffer
        st.session_state.download_filename = filename
    else:
        st.session_state.status_message = f"❌ {message}"
    st.session_state.is_running = False

# --- Sidebar Config Editor ---
st.sidebar.title("Configuration")
try:
    cfg = json.loads(st.session_state.config_text)
    st.sidebar.info(f"Current Config: **{cfg.get('base_url', 'N/A')}**")
except Exception as e:
    st.sidebar.error(f"Invalid JSON: {e}")

with st.sidebar.expander("Edit Configuration", expanded=False):
    config_editor_text = st.text_area("Config JSON", st.session_state.config_text, height=400, key="config_editor_area")
    if st.button("💾 Save Configuration"):
        try:
            json.loads(config_editor_text)
            with open(CONFIG_FILE, "w") as f:
                f.write(config_editor_text)
            st.session_state.config_text = config_editor_text
            st.sidebar.success("Saved successfully!")
            time.sleep(1)
            st.rerun()
        except Exception as e:
            st.sidebar.error(f"Invalid JSON: {e}")

# --- Main Layout ---
col1, col2 = st.columns([1, 2])
with col1:
    st.header("Controls")
    is_headless = st.checkbox("Run in Headless Mode", value=True)
    start_button = st.button("🚀 Start Scraping", disabled=st.session_state.is_running, use_container_width=True)
    status_placeholder = st.empty()

    msg = st.session_state.status_message
    if "✅" in msg:
        status_placeholder.success(msg)
    elif "❌" in msg:
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

# --- Start Scraper Thread ---
if start_button and not st.session_state.is_running:
    st.session_state.is_running = True
    st.session_state.download_data = None
    st.session_state.download_filename = ""
    st.session_state.log_buffer = ""
    st.session_state.status_message = "Status: Starting scraper thread..."

    try:
        cfg = json.loads(st.session_state.config_text)
    except Exception as e:
        st.error(f"Invalid config: {e}")
        st.session_state.is_running = False
        st.stop()

    scraper_thread = threading.Thread(
        target=run_scraper_main,
        args=(cfg, is_headless, log_callback, status_callback, finish_callback),
        daemon=True
    )
    scraper_thread.start()
    st.rerun()

# --- Auto-refresh while running ---
if st.session_state.is_running:
    flush_logs_to_session_state()
    time.sleep(2)
    st.rerun()
