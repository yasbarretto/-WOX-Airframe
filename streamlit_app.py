import streamlit as st
import time
import threading
import queue
import pandas as pd
from bs4 import BeautifulSoup
import re
import os
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager

# =========================================================
# GLOBAL QUEUE (thread-safe, independent of Streamlit)
# =========================================================
LOG_QUEUE = queue.Queue()

def log(msg: str):
    """Write a message to both console and thread-safe queue."""
    timestamp = time.strftime("[%H:%M:%S]")
    text = f"{timestamp} {msg}"
    print(text)
    LOG_QUEUE.put(text)

# =========================================================
# Scraper logic
# =========================================================
BASE_URL = "https://www.seismic.com/customer-stories/"
MAX_RETRIES = 3
SECTION_KEYWORDS = {"challenge", "solution", "headquarters", "industry", "integrations", "share", "results"}

def get_story_links(driver, wait):
    log(f"Navigating to {BASE_URL} to find links...")
    driver.get(BASE_URL)
    links = set()
    try:
        cookie_wait = WebDriverWait(driver, 5)
        cookie_button = cookie_wait.until(EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler")))
        cookie_button.click()
        log("  Accepted cookie banner.")
    except Exception:
        log("  No cookie banner found, continuing...")

    for page_num in range(1, 7):
        log(f"  Scraping page {page_num}...")
        try:
            if page_num > 1:
                btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, f'a[data-page="{page_num}"]')))
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(1)
            soup = BeautifulSoup(driver.page_source, "html.parser")
            ul = soup.find("ul", class_=lambda c: c and "grid-cols-1" in c)
            if not ul:
                continue
            found = 0
            for a in ul.find_all("a", href=True):
                href = a["href"]
                if "seismic.com/customer-stories/" in href and len(href.split("/")) > 5:
                    if href not in links:
                        links.add(href)
                        found += 1
            log(f"    Found {found} links on this page.")
        except Exception:
            log(f"    Skipped page {page_num}.")
    log(f"\nFound {len(links)} total unique story links.")
    return list(links)

def scrape_story_details(driver, wait, url):
    log(f"  Scraping: {url}")
    driver.get(url)
    time.sleep(1)
    soup = BeautifulSoup(driver.page_source, "html.parser")
    data = {"url": url}
    name = url.split("/customer-stories/")[1].strip("/")
    data["company_name"] = name.replace("-", " ").title()
    h1 = soup.find("h1")
    data["title"] = h1.get_text(strip=True) if h1 else None
    desc_div = soup.find("div", class_=lambda c: c and "lg:col-span-7" in c)
    if desc_div:
        data["description"] = " ".join(p.get_text(strip=True) for p in desc_div.find_all("p"))
    else:
        data["description"] = None
    return data

def run_scraper():
    """Background thread: logs go to LOG_QUEUE, not Streamlit."""
    log("🚀 Scraper starting...")
    driver = None
    try:
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        if os.path.exists("/usr/bin/chromium") and os.path.exists("/usr/bin/chromedriver"):
            options.binary_location = "/usr/bin/chromium"
            service = ChromeService("/usr/bin/chromedriver")
            driver = webdriver.Chrome(service=service, options=options)
            log("Detected system Chromium.")
        else:
            path = ChromeDriverManager().install()
            service = ChromeService(path)
            driver = webdriver.Chrome(service=service, options=options)
            log("Using webdriver_manager Chrome.")

        wait = WebDriverWait(driver, 20)
        urls = get_story_links(driver, wait)
        rows = []
        for url in urls:
            data = scrape_story_details(driver, wait, url)
            rows.append(data)

        if rows:
            df = pd.DataFrame(rows)
            df.to_excel("seismic_customer_stories_STREAMLIT.xlsx", index=False)
            log("✅ Saved to seismic_customer_stories_STREAMLIT.xlsx")
        else:
            log("No data scraped.")
    except Exception as e:
        log(f"❌ Fatal error: {e}")
    finally:
        if driver:
            driver.quit()
            log("Browser closed.")
        LOG_QUEUE.put("__SCRAPER_DONE__")

# =========================================================
# Streamlit UI
# =========================================================
st.set_page_config(layout="wide")
st.title("🕷️ Seismic Customer Stories Scraper")

if "log_text" not in st.session_state:
    st.session_state.log_text = ""
if "scraper_running" not in st.session_state:
    st.session_state.scraper_running = False
if "scraper_done" not in st.session_state:
    st.session_state.scraper_done = False

# --- Start button ---
if st.button("🚀 Start Scraping", use_container_width=True, disabled=st.session_state.scraper_running):
    st.session_state.scraper_running = True
    st.session_state.scraper_done = False
    st.session_state.log_text = "[00:00:00] 🚀 Scraper starting...\n"
    threading.Thread(target=run_scraper, daemon=True).start()
    st.rerun()

# --- Drain queue into UI log ---
while not LOG_QUEUE.empty():
    msg = LOG_QUEUE.get()
    if msg == "__SCRAPER_DONE__":
        st.session_state.scraper_running = False
        st.session_state.scraper_done = True
    else:
        st.session_state.log_text += msg + "\n"

# --- Display logs ---
st.markdown(
    f"<pre style='white-space:pre-wrap;background:#111;color:#0f0;"
    f"padding:10px;border-radius:8px;height:500px;overflow-y:scroll;'>"
    f"{st.session_state.log_text}</pre>",
    unsafe_allow_html=True,
)

# --- Refresh during scrape ---
if st.session_state.scraper_running:
    def _refresh():
        time.sleep(2)
        st.rerun()
    threading.Thread(target=_refresh, daemon=True).start()

# --- When done ---
if st.session_state.scraper_done and os.path.exists("seismic_customer_stories_STREAMLIT.xlsx"):
    with open("seismic_customer_stories_STREAMLIT.xlsx", "rb") as f:
        st.success("✅ Scraping complete! File saved successfully.")
        st.download_button(
            "📥 Download Excel Results",
            f,
            file_name="seismic_customer_stories_STREAMLIT.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
