import streamlit as st
import time
import pandas as pd
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import re
import json
import os # To check for config file
from io import BytesIO # To create download button for Excel
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

# --- Configuration Loading ---
CONFIG_FILE = 'config.json'
SECTION_KEYWORDS = {"challenge", "solution", "headquarters", "industry", "integrations", "share", "results", "the", "about", "at", "group", "financial"}

# --- Scraper Helper Functions ---
# (These are the same functions from the working script)
def clean_text(text):
    if not text: return None
    text = text.replace("(Opens in a new tab)", "").strip().strip('“”"\'')
    return text.strip()
def get_text(element): return element.get_text(strip=True) if element else None
def get_first_p_before_heading_strict(element, soup):
    if not element: return None
    first_section = soup.find(['h3', 'strong'], string=re.compile(r'challenge|solution|results', re.I))
    is_before = True
    if first_section: head_parent = first_section.find_parent(['div','p']);
    if first_section and head_parent and not (element.compare_position(head_parent) & 4): is_before = False
    if is_before: first_p = element.find('p');
    if is_before and first_p: p_text = first_p.get_text(strip=True);
    if is_before and first_p and len(p_text) > 20 and p_text.lower() not in SECTION_KEYWORDS: return p_text
    return None
def get_next_p_after_title_strict(element, soup):
    if not element: return None
    first_section = soup.find(['h3', 'strong'], string=re.compile(r'challenge|solution|results', re.I))
    potential_p = element.find_next_sibling('p')
    if not potential_p and element.find_next_sibling('div'): potential_p = element.find_next_sibling('div').find('p')
    if potential_p:
        is_before = True
        if first_section: head_parent = first_section.find_parent(['div','p']);
        if first_section and head_parent and not (potential_p.compare_position(heading_parent) & 4): is_before = False
        if is_before:
            p_text = potential_p.get_text(strip=True)
            if len(p_text) > 50 and p_text.lower() not in SECTION_KEYWORDS and not re.match(r'^[\w\s\'.-]+[,–—\-]', p_text): return p_text
    return None
def get_text_after_key_h3_rte(element):
    if element: value_div = element.find_next_sibling('div', class_='rte'); return value_div.get_text(strip=True) if value_div else None
    return None
def get_text_after_strong_sep_p(element):
     if element: key_p = element.find_parent('p');
     if element and key_p and element.get_text(strip=True)==key_p.get_text(strip=True): value_p = key_p.find_next_sibling('p'); return value_p.get_text(strip=True) if value_p else None
     return None
def get_text_after_strong_same_p(element):
     if element: full_p = element.find_parent('p');
     if element and full_p: full_text = full_p.get_text(strip=True); key_text = element.get_text(strip=True); value_text = full_text.replace(key_text, '', 1).strip(); return value_text if value_text else None
     return None
def get_text_after_strong_spacey_rte(element):
    if element: parent_div = element.find_parent('div', class_='space-y-8');
    if element and parent_div: value_div = element.find_next_sibling('div', class_='rte'); return value_div.get_text(strip=True) if value_div else None
    return None

EXTRACTION_METHODS = {
    "text": get_text, "first_p_before_heading_strict": get_first_p_before_heading_strict,
    "next_p_after_title_strict": get_next_p_after_title_strict, "text_after_key_h3_rte": get_text_after_key_h3_rte,
    "text_after_strong_in_separate_p": get_text_after_strong_sep_p, "parent_text_after_strong": get_text_after_strong_same_p,
    "text_after_strong_spacey_rte": get_text_after_strong_spacey_rte,
}
# --- End Scraper Helper Functions ---


# --- Scraper Core Logic ---
def get_story_links(driver, wait, config, log_callback):
    """ Step 1: Find story links, handle pagination. """
    base_url = config['base_url']; max_pages_or_clicks = config['max_pages_to_scrape']
    pagination_type = config['pagination_type']; next_page_selector_template = config['next_page_button_selector']
    list_selector = config['story_card_list_selector']; link_selector = config['story_card_link_selector']
    log_callback(f"Navigating to {base_url} to find links..."); driver.get(base_url); time.sleep(3)
    links = set()
    first_link_full_selector = f"{list_selector} {link_selector}:first-of-type"

    try:
        cookie_wait = WebDriverWait(driver, 10)
        cookie_button = cookie_wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'accept all') or contains(@id, 'accept') or contains(@class, 'accept')] | //a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'accept all')]")))
        log_callback("  Found and clicked cookie accept button."); driver.execute_script("arguments[0].click();", cookie_button); time.sleep(2)
    except (TimeoutException, NoSuchElementException): log_callback("  No obvious cookie banner found or timed out. Continuing...")

    try:
         wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, list_selector)))
         log_callback(f"  Initial container ('{list_selector}') found."); wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, first_link_full_selector)))
         log_callback(f"  First link ('{first_link_full_selector}') visible."); time.sleep(1)
    except TimeoutException: log_callback(f"Error: Initial container/link not found/visible. Cannot proceed."); return []

    current_page = 1; pagination_active = True; last_first_href = ""
    while pagination_active and current_page <= max_pages_or_clicks:
        log_callback(f"--- Processing Page {current_page} ---"); found_on_page = 0
        current_first_href = None
        try:
            first_link_element = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, first_link_full_selector)))
            current_first_href = first_link_element.get_attribute('data-href') or first_link_element.get_attribute('href')
            time.sleep(2)
            if current_page > 1 and current_first_href == last_first_href: log_callback(f"    Warning: Content appears unchanged after pagination. Stopping."); break
            last_first_href = current_first_href
            link_elements = driver.find_elements(By.CSS_SELECTOR, f"{list_selector} {link_selector}")
            log_callback(f"  Found {len(link_elements)} potential link elements.");
            for element in link_elements:
                try:
                    href = element.get_attribute('data-href') or element.get_attribute('href')
                    if href:
                        if href.endswith('/.html'): href = href[:-6]
                        elif href.endswith('.html'): href = href[:-5]
                        full_url = urljoin(base_url, href)
                        if urljoin(base_url, '/') in full_url and full_url != base_url and href != '#':
                            company_regex = config.get('data_selectors',{}).get('company_name',{}).get('regex', '/stories/([^/]+)/')
                            path_start_segment = company_regex.split('/')[1]; path_start = base_url.rsplit('/', 2)[0] + f'/{path_start_segment}/'
                            if full_url.startswith(path_start):
                                if full_url not in links: links.add(full_url); found_on_page += 1
                except Exception as link_err: log_callback(f"    Error processing link element: {link_err}")
            log_callback(f"  Found {found_on_page} new unique links on page view.")
            if not link_elements and current_page == 1: log_callback(" Error: No links found on initial page. Exiting."); return []

            if current_page < max_pages_or_clicks:
                if pagination_type == 'click_button_by_page_number' and next_page_selector_template:
                    next_page_num_str = str(current_page + 1); page_selector = next_page_selector_template.format(page_num=next_page_num_str)
                    log_callback(f"  Attempting pagination click for page {next_page_num_str}...")
                    
                    # Scroll to pagination - find a common pagination container
                    nav_element = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "nav, [class*='pagination']")))
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", nav_element); time.sleep(0.5)
                    log_callback(f"    Pagination container found and scrolled to.")
                    
                    page_button = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.CSS_SELECTOR, page_selector)))
                    driver.execute_script("arguments[0].click();", page_button); log_callback(f"  Clicked page {next_page_num_str} link.")
                    WebDriverWait(driver, 30).until(EC.visibility_of_element_located((By.CSS_SELECTOR, first_link_full_selector)))
                    WebDriverWait(driver, 10).until(lambda d: (d.find_element(By.CSS_SELECTOR, first_link_full_selector).get_attribute('data-href') or d.find_element(By.CSS_SELECTOR, first_link_full_selector).get_attribute('href')) != last_first_href)
                    log_callback(f"  Page {next_page_num_str} appears loaded."); time.sleep(3)
                elif pagination_type == 'click_load_more' and next_page_selector_template:
                     log_callback(f"  Attempting 'Load More' (Click {current_page})...");
                     load_more_button = WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.CSS_SELECTOR, next_page_selector_template)))
                     current_items_selector = f"{list_selector} {link_selector}"; initial_item_count = len(driver.find_elements(By.CSS_SELECTOR, current_items_selector))
                     log_callback(f"    Click {current_page}: Found button. Current items: {initial_item_count}. Clicking...")
                     driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", load_more_button); time.sleep(0.5)
                     try: load_more_button.click()
                     except ElementClickInterceptedException: log_callback("      Std click intercepted, trying JS."); driver.execute_script("arguments[0].click();", load_more_button)
                     WebDriverWait(driver, 60).until(lambda d: len(d.find_elements(By.CSS_SELECTOR, current_items_selector)) > initial_item_count);
                     new_item_count = len(driver.find_elements(By.CSS_SELECTOR, current_items_selector))
                     log_callback(f"    Load More Click {current_page}: Items increased to {new_item_count}."); time.sleep(1)
                else: pagination_active = False; log_callback("  Pagination finished or not configured.")
            if current_page >= max_pages_or_clicks: pagination_active = False
            current_page += 1
        except (TimeoutException, NoSuchElementException) as e: log_callback(f"  Error processing page {current_page}: Timeout/Element not found. {e}"); pagination_active = False; log_callback("  Stopping pagination.")
        except Exception as e: log_callback(f"  Unexpected error processing page {current_page}: {e}"); pagination_active = False; log_callback("  Stopping pagination.")
    log_callback(f"\nFound {len(links)} total unique story links.")
    return list(links)

def scrape_story_details(driver, wait, story_url, config, log_callback):
    """ Step 2: Scrapes details based on config, calculates confidence. """
    log_callback(f"  Scraping: {story_url}"); load_timeout = config.get('details_page_load_timeout', 180)
    wait_selector = config.get('wait_for_element_selector', 'body'); data_selectors = config.get('data_selectors', {})
    conf_thresholds = config.get('confidence_thresholds', {"high": 7, "medium": 4})
    driver.set_page_load_timeout(load_timeout)
    try:
        driver.get(story_url); wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, wait_selector)))
    except TimeoutException: log_callback(f"    Page {story_url} timed out waiting for '{wait_selector}' and was skipped."); return None
    except (WebDriverException, Exception) as e: log_callback(f"    Error loading {story_url}: {e}"); return None
    finally:
        try: driver.set_page_load_timeout(60) # Reset
        except WebDriverException as e: log_callback(f"    Warning: Could not reset page load timeout after {story_url}. Error: {e}")

    soup = BeautifulSoup(driver.page_source, 'html.parser'); story_data = {'url': story_url}
    confidence_points = 0; title_element = None
    for field, selectors in data_selectors.items():
        field_value, highest_confidence = None, 0
        if field == 'company_name' and selectors.get("source") == "url":
            try: match = re.search(selectors.get("regex", ""), story_url);
            except Exception as e: log_callback(f"    Error parsing company name: {e}")
            if match and match.group(1): name = match.group(1).replace('-', ' ').title(); field_value = name; highest_confidence = 2
            story_data[field] = field_value; confidence_points += highest_confidence; continue
        if not isinstance(selectors, list): log_callback(f"    Warning: Selectors for '{field}' not a list. Skipping."); continue
        for selector_config in selectors:
            selector, method_name = selector_config.get('selector'), selector_config.get('method')
            key_text, limit = selector_config.get('key_text'), selector_config.get('limit')
            min_length, confidence = selector_config.get('min_length', 0), selector_config.get('confidence', 0)
            if not selector or not method_name: log_callback(f"    Warning: Invalid selector config for {field}."); continue
            element, elements = None, []
            if selector == 'title_element': element = title_element;
            if selector == 'title_element' and not element: continue;
            if selector == 'title_element': elements = [element]
            else:
                try:
                    if key_text:
                         tag_name = 'strong';
                         if method_name == "text_after_key_h3_rte": tag_name = 'h3'
                         elif method_name == "next_p_after_title_strict": tag_name = selector
                         elements = soup.find_all(tag_name, string=re.compile(rf'^\s*{key_text}\s*$', re.I))
                    elif limit: elements = soup.select(selector, limit=limit)
                    else: elements = soup.select(selector)
                    if not elements: continue
                    element = elements[0]
                except Exception as e: log_callback(f"    Error finding element '{selector}'/'{key_text}' for {field}: {e}"); continue
            extraction_func = EXTRACTION_METHODS.get(method_name)
            if extraction_func:
                try:
                    extracted_text = None
                    if method_name in ['first_p_before_heading_strict', 'next_p_after_title_strict']: extracted_text = extraction_func(element, soup)
                    else: extracted_text = extraction_func(element)
                    cleaned_value = clean_text(extracted_text)
                    if cleaned_value and len(cleaned_value) >= min_length and cleaned_value.lower() not in SECTION_KEYWORDS:
                        field_value = cleaned_value; highest_confidence = max(highest_confidence, confidence)
                        if field == 'title' and element: title_element = element
                        break
                except Exception as e: log_callback(f"    Error applying method '{method_name}' for {field}: {e}")
            else: log_callback(f"    Warning: Unknown method '{method_name}' for {field}.")
        story_data[field] = field_value
        if field_value: confidence_points += highest_confidence
    high_threshold, medium_threshold = conf_thresholds.get('high', 7), conf_thresholds.get('medium', 4)
    if confidence_points >= high_threshold: story_data['confidence_score'] = 'High'; story_data['needs_verification'] = 'No'
    elif confidence_points >= medium_threshold: story_data['confidence_score'] = 'Medium'; story_data['needs_verification'] = 'Yes'
    else: story_data['confidence_score'] = 'Low'; story_data['needs_verification'] = 'Yes'
    return story_data


# --- Main Scraper Runner ---
def run_scraper_main(config, is_headless, log_callback, status_callback, finish_callback):
    """
    Main function to initialize driver and run the scraping process.
    """
    driver = None
    try:
        log_callback("Initializing browser...")
        status_callback("Initializing browser...")

        options = ChromeOptions()
        options.set_capability("pageLoadStrategy", "eager")
        if is_headless:
            options.add_argument('--headless')
            options.add_argument('--disable-gpu') # Often needed for headless
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        options.add_argument('--disable-extensions')
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--blink-settings=imagesEnabled=false')
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--incognito")
        options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument('--disable-blink-features=AutomationControlled')
        
        # --- Driver Setup ---
        if 'STREAMLIT_SERVER_PORT' in os.environ:
            log_callback("Running in Streamlit Cloud environment...")
            service = ChromeService(executable_path="/usr/bin/chromium-driver")
            driver = webdriver.Chrome(service=service, options=options)
        else:
            log_callback("Running in local environment...")
            driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=options)
        
        wait_timeout = config.get('main_wait_timeout', 180)
        wait = WebDriverWait(driver, wait_timeout)

    except Exception as e:
        log_callback(f"Error setting up Selenium WebDriver: {e}")
        log_callback("Ensure Google Chrome/Chromium and chromedriver are installed.")
        finish_callback(False, f"WebDriver Error: {e}")
        return

    # --- Start Scraping Process ---
    all_stories_data = []
    try:
        status_callback("Finding links...")
        story_urls = get_story_links(driver, wait, config, log_callback) # Pass log_callback

        if story_urls:
            log_callback(f"\nStarting to scrape {len(story_urls)} individual story pages...")
            status_callback(f"Scraping 0 / {len(story_urls)} pages...")
            urls_to_scrape = list(story_urls)
            max_retries = config.get('max_retries', 3)

            for attempt in range(max_retries):
                if not urls_to_scrape: log_callback("\nAll URLs scraped successfully."); break
                log_callback(f"\n--- Scraping Pass {attempt + 1} of {max_retries} ---")
                log_callback(f"Attempting to scrape {len(urls_to_scrape)} URLs...")
                failed_urls_this_pass = []
                for i, url in enumerate(urls_to_scrape):
                    status_callback(f"Pass {attempt+1}: Scraping {i+1} / {len(urls_to_scrape)}...")
                    try: _ = driver.window_handles # Check session
                    except WebDriverException:
                         log_callback("Browser session lost. Attempting to restart...");
                         try: driver.quit()
                         except: pass
                         if 'STREAMLIT_SERVER_PORT' in os.environ: service = ChromeService(executable_path="/usr/bin/chromium-driver"); driver = webdriver.Chrome(service=service, options=options)
                         else: driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=options)
                         wait = WebDriverWait(driver, wait_timeout); log_callback("Browser restarted.")

                    data = scrape_story_details(driver, wait, url, config, log_callback) # Pass log_callback
                    if data: all_stories_data.append(data)
                    else: failed_urls_this_pass.append(url)
                urls_to_scrape = failed_urls_this_pass
                if urls_to_scrape: log_callback(f"    {len(urls_to_scrape)} URLs failed. Pausing before retry..."); time.sleep(5)
            if urls_to_scrape: log_callback(f"\nWarning: {len(urls_to_scrape)} URLs failed after {max_retries} attempts."); log_callback(f"Failed URLs: {urls_to_scrape}")

            # Step 3: Save data
            if all_stories_data:
                log_callback(f"\nScraping complete. Successfully scraped {len(all_stories_data)} URLs.")
                log_callback("Preparing data for download..."); status_callback("Preparing download...")
                all_stories_data.sort(key=lambda x: x.get('url', ''))
                df_stories = pd.DataFrame(all_stories_data)
                output_columns = config.get('output_columns', list(df_stories.columns))
                if 'confidence_score' not in df_stories.columns: df_stories['confidence_score'] = 'Low'
                if 'needs_verification' not in df_stories.columns: df_stories['needs_verification'] = 'Yes'
                if 'confidence_score' not in output_columns: output_columns.append('confidence_score')
                if 'needs_verification' not in output_columns: output_columns.append('needs_verification')
                existing_cols = [col for col in output_columns if col in df_stories.columns]
                df_stories = df_stories[existing_cols]; df_stories = df_stories.reindex(columns=output_columns)
                output_buffer = BytesIO()
                with pd.ExcelWriter(output_buffer, engine='openpyxl') as writer:
                    df_stories.to_excel(writer, sheet_name='Stories', index=False)
                output_buffer.seek(0)
                output_filename = config.get('output_filename', 'scraped_data.xlsx')
                finish_callback(True, f"Success! Scraped {len(all_stories_data)} items.", output_buffer, output_filename)
            else: log_callback("No data was scraped."); finish_callback(False, "Scraping finished, but no data was saved.")
        else: log_callback("No story links were found. Exiting."); finish_callback(False, "No story links found.")
    except Exception as e: log_callback(f"An unexpected error occurred: {e}"); finish_callback(False, f"An error occurred: {e}")
    finally:
        try: driver.quit(); log_callback("Browser closed.")
        except WebDriverException as e: log_callback(f"Browser already closed: {e}")
        except Exception as e: log_callback(f"Error closing browser: {e}")


# --- Streamlit GUI ---
st.set_page_config(layout="wide")
st.title("🤖 Configurable Web Scraper")

# --- NEW: Config Editor ---
st.sidebar.title("Configuration")

# Load config from file into session state ONCE
if 'config_text' not in st.session_state:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            st.session_state.config_text = f.read()
    else:
        # Provide a default empty template if no file
        st.session_state.config_text = json.dumps({
            "base_url": "https://www.example.com/stories/",
            "max_pages_to_scrape": 1,
            "pagination_type": "none",
            "next_page_button_selector": "",
            "story_card_list_selector": "body",
            "story_card_link_selector": "a",
            "wait_for_element_selector": "h1",
            "details_page_load_timeout": 180,
            "main_wait_timeout": 180,
            "max_retries": 3,
            "output_filename": "scraped_data.xlsx",
            "data_selectors": {
                "company_name": {"source": "url", "regex": "/stories/([^/]+)/?"},
                "title": [{"selector": "h1", "method": "text", "confidence": 2}]
            },
            "confidence_thresholds": {"high": 2, "medium": 1},
            "output_columns": ["url", "company_name", "title", "confidence_score", "needs_verification"]
        }, indent=2)

with st.sidebar.expander("Edit Configuration File (`config.json`)", expanded=False):
    # The text editor's value is now controlled by session state
    config_editor_text = st.text_area("Config JSON", st.session_state.config_text, height=400, key="config_editor_area")
    
    if st.button("Save Configuration"):
        try:
            # Test if JSON is valid
            json.loads(config_editor_text)
            # Save the file
            with open(CONFIG_FILE, 'w') as f:
                f.write(config_editor_text)
            # Update session state
            st.session_state.config_text = config_editor_text
            st.sidebar.success("Configuration saved successfully!")
            time.sleep(1) # Pause to show message
            st.rerun() # Rerun to reload config
        except json.JSONDecodeError as e:
            st.sidebar.error(f"Invalid JSON: {e}")
        except Exception as e:
            st.sidebar.error(f"Error saving file: {e}")

# --- End Config Editor ---

# Load config data for the app from session state
try:
    config_data = json.loads(st.session_state.config_text)
    st.sidebar.info(f"Loaded config for: **{config_data.get('base_url', 'N/A')}**")
except Exception as e:
    st.error(f"Error: Could not load config from text. Please fix JSON in the editor. Error: {e}")
    st.stop()


# --- Main App Area ---
col1, col2 = st.columns([1, 2])

with col1:
    st.header("Controls")
    is_headless = st.checkbox("Run in Headless Mode (invisible browser)", value=True, help="Recommended for servers. Uncheck to watch the scraper work (local only).")
    
    # Initialize session state
    if 'is_running' not in st.session_state:
        st.session_state.is_running = False
    if 'download_data' not in st.session_state:
        st.session_state.download_data = None
    if 'download_filename' not in st.session_state:
        st.session_state.download_filename = ""
    if 'log_buffer' not in st.session_state:
        st.session_state.log_buffer = ""
    if 'status_message' not in st.session_state:
        st.session_state.status_message = f"Status: Idle. Ready to scrape {config_data.get('base_url')}."

    start_button = st.button("🚀 Start Scraping", disabled=st.session_state.is_running, use_container_width=True)
    status_placeholder = st.empty()
    status_placeholder.info(st.session_state.status_message) # Display current status

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
    # *** FIX: Define the log area ONCE using the session state buffer ***
    log_placeholder = st.empty()
    log_placeholder.text_area("Log Output", st.session_state.log_buffer, height=400, key="log_output_main")

# --- Callback Functions for the Thread ---
# These functions will be called from the background thread
@st.cache_data # Caching the logger object
def get_logger(placeholder):
    # This is a bit of a hack to pass the UI element to the thread
    # A more robust way involves Streamlit Components or advanced state management
    class StreamlitLog:
        def __init__(self, placeholder_key):
            self.placeholder_key = placeholder_key
            if 'log_buffer' not in st.session_state:
                 st.session_state.log_buffer = ""

        def __call__(self, message):
            print(message) # Log to console
            st.session_state.log_buffer += message + "\n" # Update state
        
        def clear(self):
            st.session_state.log_buffer = ""

    return StreamlitLog("log_output_main")

log_callback = get_logger(log_placeholder)

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
    # No rerun here, let the loop handle it

# --- Start Button Logic ---
if start_button and not st.session_state.is_running:
    st.session_state.is_running = True
    st.session_state.download_data = None
    st.session_state.download_filename = ""
    st.session_state.log_buffer = "" # Clear log buffer
    st.session_state.status_message = "Status: Starting scraper thread..."
    
    # Reload config from the editor state
    try:
        current_config_data = json.loads(st.session_state.config_text)
    except Exception as e:
        st.error(f"Cannot start: Invalid JSON in config editor. {e}")
        st.session_state.is_running = False
        st.stop()

    scraper_thread = threading.Thread(
        target=run_scraper_main,
        args=(current_config_data, is_headless, log_callback, status_callback, finish_callback),
        daemon=True
    )
    scraper_thread.start()
    st.rerun() # Rerun to update button state and status

# --- Auto-refresh loop for live log (while running) ---
if st.session_state.is_running:
    # Update the log placeholder with the current buffer
    log_placeholder.text_area("Log Output", st.session_state.log_buffer, height=400, key="log_output_main")
    status_placeholder.info(st.session_state.status_message)
    time.sleep(2) # Refresh every 2 seconds
    st.rerun()
