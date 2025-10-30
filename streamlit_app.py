def run_scraper():
    import os

    print("🚀 Scraper starting...")
    print("Step 1: Initializing Chrome driver ...")
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

        driver = None

        # --- Auto-select Chrome driver depending on environment ---
        try:
            # 🧩 Option 1: Linux/Streamlit Cloud (preinstalled Chromium + chromedriver)
            if os.path.exists("/usr/bin/chromium") and os.path.exists("/usr/bin/chromedriver"):
                print("Detected system Chromium — using /usr/bin/chromedriver")
                options.binary_location = "/usr/bin/chromium"
                service = ChromeService(executable_path="/usr/bin/chromedriver")
                driver = webdriver.Chrome(service=service, options=options)

            else:
                # 🧩 Option 2: Regular local environment (Mac/Windows)
                from webdriver_manager.chrome import ChromeDriverManager
                driver_path = ChromeDriverManager().install()
                print(f"Using webdriver_manager installed driver: {driver_path}")
                service = ChromeService(executable_path=driver_path)
                driver = webdriver.Chrome(service=service, options=options)

        except Exception as e:
            print(f"⚠️ Standard Chrome failed: {e}")
            print("Trying fallback with undetected_chromedriver...")

            try:
                import undetected_chromedriver as uc
                driver = uc.Chrome(options=options, headless=True)
                print("✅ Using undetected_chromedriver fallback.")
            except Exception as uc_err:
                print(f"❌ Could not initialize undetected_chromedriver either: {uc_err}")
                return

        if not driver:
            print("❌ Failed to initialize any Chrome driver.")
            return

        wait = WebDriverWait(driver, WAIT_TIMEOUT)
        print("🧠 Thread launched successfully.")

    except Exception as e:
        print(f"❌ Fatal error initializing WebDriver: {e}")
        return

    # ========== Run the actual scraping ==========
    try:
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

    finally:
        try:
            driver.quit()
            print("Browser closed.")
        except Exception as e:
            print(f"Error closing browser: {e}")
