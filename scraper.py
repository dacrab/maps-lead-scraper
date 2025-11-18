from __future__ import annotations

import argparse
import csv
import json
import random
import re
import threading
import time
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page, Playwright


EMAIL_REGEX = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"

INVALID_EMAIL_PATTERNS: List[str] = [
    "example.com",
    "@example",
    ".png",
    ".jpg",
    ".gif",
    "sampleemail",
    "youremail",
    "noreply",
    "wixpress",
    "sentry",
    "qodeinteractive",
]

CONTACT_KEYWORDS: List[str] = [
    "Contact",
    "contact",
    "CONTACT",
    "Kontakt",
    "kontakt",
    "Contacto",
    "contacto",
    "Contatto",
    "contatto",
    "Contactez",
    "contactez",
    "Impressum",
    "impressum",
    "About",
    "about",
]

GOOGLE_MAPS_RESULT_SELECTORS: List[str] = [
    "a[href*='/maps/place/']",
    "div.Nv2PK a",
    "a.hfpxzc",
    "div[role='article'] a",
]

MAPS_WEBSITE_SKIP_KEYWORDS: List[str] = [
    "google",
    "facebook",
    "instagram",
    "youtube",
    "linkedin",
    "twitter",
    "gstatic",
    "googleapis",
    "schema.org",
]

SOCIAL_DOMAINS_TO_SKIP: List[str] = [
    "facebook.com",
    "linkedin.com",
    "instagram.com",
    "youtube.com",
    "twitter.com",
]

PHONE_PATTERNS: List[str] = [
    r"\+?\d{1,3}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,9}",
    r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}",
]


@dataclass
class Config:
    output_filename: str = "recipients.csv"
    search_term: str = ""
    locations: List[str] = field(default_factory=list)
    max_results_per_query: int = 0
    phone_min_digits: int = 10
    headless: bool = True
    use_threading: bool = False
    max_thread_workers: int = 3
    scroll_pause_time: float = 2.0
    max_scroll_attempts: int = 20
    delay_between_queries_seconds_min: float = 3.0
    delay_between_queries_seconds_max: float = 5.0

    @staticmethod
    def load(path: str) -> "Config":
        config_path = Path(path)
        with config_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        scroll_pause_time = float(data.get("scroll_pause_time", data.get("scroll_pause_seconds", 2.0)))
        max_scroll_attempts = int(data.get("max_scroll_attempts", data.get("scroll_passes", 20)))
        return Config(
            output_filename=str(data.get("output_filename", "recipients.csv")),
            search_term=str(data.get("search_term", "")),
            locations=list(data.get("locations", [])),
            max_results_per_query=int(data.get("max_results_per_query", 0)),
            phone_min_digits=int(data.get("phone_min_digits", 10)),
            headless=bool(data.get("headless", True)),
            use_threading=bool(data.get("use_threading", False)),
            max_thread_workers=int(data.get("max_thread_workers", 3)),
            scroll_pause_time=scroll_pause_time,
            max_scroll_attempts=max_scroll_attempts,
            delay_between_queries_seconds_min=float(data.get("delay_between_queries_seconds_min", 3.0)),
            delay_between_queries_seconds_max=float(data.get("delay_between_queries_seconds_max", 5.0)),
        )


class EmailScraper:
    def __init__(self, config: Config) -> None:
        self.emails: Dict[str, str] = {}
        self.visited_urls: Set[str] = set()
        self.company_phones: Dict[str, str] = {}
        self.config = config
        self.use_threading = config.use_threading
        self.headless = config.headless
        self.output_filename = Path(config.output_filename)
        self.phone_min_digits = config.phone_min_digits
        self.max_thread_workers = config.max_thread_workers
        self.lock = threading.Lock()
        # Playwright handles Chromium download; ensure browser is present
        self.pw: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._init_playwright_with_fallback()
        self._load_existing_data()

    def _load_existing_data(self) -> None:
        if not self.output_filename.exists():
            return
            
        print(f"[*] Loading existing data from {self.output_filename}...")
        try:
            with self.output_filename.open("r", encoding="utf-8") as f:
                reader = csv.reader(f)
                headers = next(reader, None) # Skip header
                count = 0
                for row in reader:
                    if len(row) < 4:
                        continue
                    # Format: Company, Email, Phone, Website
                    try:
                        _, email, phone, website = row[:4]
                    except ValueError:
                        continue
                    
                    if email:
                        self.emails[email] = website
                    if website:
                        self.visited_urls.add(website)
                        if phone:
                            self.company_phones[website] = phone
                    count += 1
                print(f"[*] Loaded {count} existing records. Resuming...")
        except Exception as e:
            print(f"[!] Failed to load existing data: {e}")

    def _init_playwright_with_fallback(self) -> None:
        import subprocess
        self.pw = sync_playwright().start()
        launch_args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--disable-notifications",
            "--disable-popup-blocking",
        ]
        try:
            self.browser = self.pw.chromium.launch(headless=self.headless, args=launch_args)
        except Exception:
            # Attempt auto-install then retry once
            try:
                subprocess.run([
                    "python",
                    "-m",
                    "playwright",
                    "install",
                    "--with-deps",
                    "chromium",
                ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except Exception:
                pass
            self.browser = self.pw.chromium.launch(headless=self.headless, args=launch_args)
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        self.context = self.browser.new_context(user_agent=ua, java_script_enabled=True)
        # Optimize: Block resources to speed up scraping
        self.context.route("**/*.{png,jpg,jpeg,gif,webp,svg,css,woff,woff2,mp4,mp3}", lambda route: route.abort())
        self.page = self.context.new_page()

    def _accept_gmaps_cookies(self) -> None:
        from playwright.sync_api import Frame

        page = self.page
        if page is None:
            return

        selectors = [
            "button[aria-label='Accept all']",
            "button[aria-label='I agree']",
            "button[aria-label='Accept all cookies']",
            "button[jsname='b3VHJd']",
        ]
        texts = ["Accept all", "I agree", "Accept cookies", "Agree to all"]

        def try_click_on(target: Page | Frame) -> bool:
            for selector in selectors:
                try:
                    el = target.query_selector(selector)
                    if el:
                        el.click()
                        time.sleep(1.5)
                        return True
                except Exception:
                    continue
            for text in texts:
                try:
                    el = target.query_selector(f"//button[contains(., '{text}')]")
                    if el:
                        el.click()
                        time.sleep(1.5)
                        return True
                except Exception:
                    continue
            return False

        try:
            if try_click_on(page):
                return
            for iframe in page.query_selector_all("iframe"):
                try:
                    inner = iframe.content_frame()
                    if inner and try_click_on(inner):
                        break
                except Exception:
                    continue
        except Exception:
            pass

    def extract_emails(self, text: str) -> List[str]:
        found_emails = re.findall(EMAIL_REGEX, text, re.IGNORECASE)
        filtered: List[str] = []
        for email in found_emails:
            email_lower = email.lower()
            if any(inv in email_lower for inv in INVALID_EMAIL_PATTERNS):
                continue
            filtered.append(email)
        return filtered

    def extract_phone(self, text: str) -> Optional[str]:
        for pattern in PHONE_PATTERNS:
            matches = re.findall(pattern, text)
            for match in matches:
                digits_only = re.sub(r"\D", "", match)
                if len(digits_only) >= self.phone_min_digits and len(digits_only) <= 15:
                    if not self._is_invalid_phone(digits_only):
                        return self._format_phone(match)
        return None

    @staticmethod
    def _is_invalid_phone(digits: str) -> bool:
        if len(digits) == 8 and digits.isdigit():
            if 1900 <= int(digits[:4]) <= 2100:
                return True
        if len(digits) > 15:
            return True
        if len(set(digits)) == 1:
            return True
        return False

    @staticmethod
    def _format_phone(phone: str) -> str:
        digits = re.sub(r"\D", "", phone)
        if len(digits) == 10:
            return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
        elif len(digits) == 11 and digits[0] == '1':
            return f"+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
        return phone

    def scrape_google_maps(self, query: str, max_results: int = 0, scroll_pause: float = 2, max_scrolls: int = 20) -> List[str]:
        if not self.page:
            raise RuntimeError("Browser is not initialized for Google Maps scraping.")
        print(f"\n[*] Google Maps search: '{query}'")
        websites: List[str] = []
        all_result_urls: Set[str] = set()
        try:
            page = self.page
            assert page is not None
            search_url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}"
            page.goto(search_url, wait_until="domcontentloaded")
            self._accept_gmaps_cookies()
            print("[*] Waiting for search results...")
            time.sleep(4)
            self._accept_gmaps_cookies()
            print("[*] Scraping all available results (scrolling to load more)...")
            result_selector = None
            for selector in GOOGLE_MAPS_RESULT_SELECTORS:
                try:
                    links = page.query_selector_all(selector)
                    if links:
                        result_selector = selector
                        print(f"   [+] Using selector: {selector}")
                        break
                except Exception:
                    continue
            if not result_selector:
                print("[!] Could not find results panel")
                return websites
            last_count = 0
            scroll_attempts = 0
            no_new_results_count = 0
            while scroll_attempts < max_scrolls:
                try:
                    result_links = page.query_selector_all(result_selector)
                    current_urls = set()
                    for el in result_links:
                        try:
                            href = el.get_attribute("href")
                            if href and "/maps/place/" in href:
                                current_urls.add(href)
                        except Exception:
                            continue
                    all_result_urls.update(current_urls)
                    current_count = len(all_result_urls)
                    if current_count > last_count:
                        print(f"   [>] Found {current_count} results so far...")
                        last_count = current_count
                        no_new_results_count = 0
                    else:
                        no_new_results_count += 1
                        if no_new_results_count >= 3:
                            print(f"   [+] No more results found after {scroll_attempts} scrolls")
                            break
                    panel = page.query_selector("div[role='feed']")
                    if panel:
                        page.evaluate("el => el.scrollTop = el.scrollHeight", panel)
                    time.sleep(scroll_pause)
                    scroll_attempts += 1
                except Exception:
                    try:
                        page.evaluate("window.scrollBy(0, 1000)")
                        time.sleep(scroll_pause)
                        scroll_attempts += 1
                    except Exception:
                        break
            result_urls = list(all_result_urls)
            if max_results > 0:
                result_urls = result_urls[:max_results]
            print(f"[+] Collected {len(result_urls)} unique results")
            for i, place_url in enumerate(result_urls, 1):
                try:
                    print(f"\n   Company {i}/{len(result_urls)}")
                    page.goto(place_url, wait_until="domcontentloaded")
                    time.sleep(2)
                    page_html = page.content()
                    maps_emails = self.extract_emails(page_html)
                    maps_phone = self.extract_phone(page_html)
                    if maps_emails:
                        self._record_contact_details(place_url, maps_emails, maps_phone)
                    website_pattern = (
                        r'https?://(?!.*google\.com|.*facebook\.com|.*instagram\.com)'
                        r"[a-zA-Z0-9-]+\.[a-zA-Z]{2,}(?:/[^\s\"<>]*)?"
                    )
                    matches = re.findall(website_pattern, page_html)
                    website_url = next(
                        (m for m in matches if not any(s in m.lower() for s in MAPS_WEBSITE_SKIP_KEYWORDS)),
                        None
                    )
                    if not website_url:
                        try:
                            wb = page.query_selector("a[data-item-id='authority']")
                            website_url = wb.get_attribute("href") if wb else None
                        except Exception:
                            website_url = None
                    if website_url:
                        if "/url?q=" in website_url:
                            website_url = website_url.split("/url?q=", 1)[1].split("&")[0]
                        if any(s in website_url.lower() for s in SOCIAL_DOMAINS_TO_SKIP):
                            continue
                        clean_url = website_url.split("?", 1)[0].split("#", 1)[0]
                        if clean_url not in websites:
                            websites.append(clean_url)
                            print(f"      [+] {clean_url}")
                    else:
                        print("      [!] No website found")
                except Exception as e:
                    print(f"      [X] Error: {e}")
                    continue
            print(f"\n[+] {len(websites)} websites extracted from Google Maps")
        except Exception as e:
            print(f"[X] Error with Google Maps: {str(e)}")
        return websites

    def _record_contact_details(self, url: str, emails: List[str], phone: Optional[str]) -> None:
        for email in emails:
            email_lower = email.lower().strip()
            with self.lock:
                if any(e.lower() == email_lower for e in self.emails.keys()):
                    continue
                self.emails[email.strip()] = url
                if phone and url not in self.company_phones:
                    self.company_phones[url] = phone
            print(f"   [+] Email found: {email.strip()}")

    def _try_contact_page(self, page: Page) -> None:
        for keyword in CONTACT_KEYWORDS:
            try:
                link = page.query_selector(f"a:has-text('{keyword}')")
                if not link:
                    continue
                href = link.get_attribute("href")
                if not href or not href.startswith("http"):
                    continue
                with self.lock:
                    if href in self.visited_urls:
                        continue
                    self.visited_urls.add(href)
                page.goto(href, wait_until="domcontentloaded")
                time.sleep(1)
                contact_emails = self.extract_emails(page.content())
                if contact_emails:
                    self._record_contact_details(href, contact_emails, phone=None)
                break
            except Exception:
                continue

    def scrape_website(self, url: str, driver: Optional[Page] = None) -> None:
        with self.lock:
            if url in self.visited_urls:
                return
            self.visited_urls.add(url)
        if driver is None:
            ctx = self.context
            if ctx is None:
                raise RuntimeError("Browser is not initialized for website scraping.")
            page = ctx.new_page()
        else:
            page = driver
        max_retries = 3
        retry_count = 0
        while retry_count < max_retries:
            try:
                print(f"[*] Scanning: {url}")
                page.goto(url, wait_until="domcontentloaded")
                time.sleep(2)
                page_source = page.content()
                emails = self.extract_emails(page_source)
                phone = self.extract_phone(page_source)
                if emails:
                    self._record_contact_details(url, emails, phone)
                else:
                    self._try_contact_page(page)
                break
            except Exception as e:
                msg = str(e)
                if "net::ERR_NAME_NOT_RESOLVED" in msg:
                    print("   [!] Skipping unreachable domain (DNS error)")
                    break
                retry_count += 1
                first_line = msg.splitlines()[0] if msg else "Unknown error"
                if retry_count < max_retries:
                    print(f"   [!] Error, retrying ({retry_count}/{max_retries}): {first_line}")
                    time.sleep(2)
                else:
                    print(f"   [X] Failed after {max_retries} attempts: {first_line}")
                    break
        if driver is not None:
            try:
                driver.close()
            except Exception:
                pass

    def run(self, queries: List[str], max_sites_per_query: int = 0, scroll_pause: float = 2, max_scrolls: int = 20) -> None:
        if self.use_threading:
            print("[!] Threading is not enabled in Playwright mode; proceeding single-threaded.")
        print(f"\n[>] Collecting websites from {len(queries)} search queries...")
        all_websites: List[str] = []
        for i, query in enumerate(queries, 1):
            print(f"\n{'=' * 60}")
            print(f"Search Query {i}/{len(queries)}: {query}")
            print(f"{'=' * 60}")
            websites = self.scrape_google_maps(query, max_sites_per_query, scroll_pause, max_scrolls)
            all_websites.extend(websites)
            if i < len(queries):
                wait_time = random.uniform(
                    float(self.config.delay_between_queries_seconds_min),
                    float(self.config.delay_between_queries_seconds_max),
                )
                print(f"\n[*] Waiting {wait_time:.1f} seconds...")
                time.sleep(wait_time)
        unique_websites = list(set(all_websites))
        print(f"\n{'=' * 60}")
        print(f"[>] TOTAL: {len(unique_websites)} unique websites found")
        print("[>] Starting email extraction...")
        print(f"{'=' * 60}")
        for i, url in enumerate(unique_websites, 1):
            print(f"\n[>] Website {i}/{len(unique_websites)} ({int(i / len(unique_websites) * 100)}%)")
            self.scrape_website(url)
            # Save incrementally so UI updates
            self.save_results()
            if i % 5 == 0:
                print(f"\n[>] Progress: {len(self.emails)} emails found so far")

    def save_results(self, filename: Optional[str] = None) -> str:
        if filename is None:
            filename = self.output_filename
        unique_emails: Dict[str, Dict[str, str]] = {}
        for email, source_url in self.emails.items():
            email_lower = email.lower().strip()
            if email_lower not in unique_emails:
                company_name = self._guess_company_name(email, source_url)
                phone = self.company_phones.get(source_url, "")
                unique_emails[email_lower] = {
                    "email": email.strip(),
                    "company": company_name,
                    "phone": phone,
                    "website": source_url,
                }
        # Prettier CSV: Company, Email, Phone, Website (sorted by Company then Email)
        rows = [[d["company"], d["email"], d["phone"], d["website"]] for d in unique_emails.values()]
        rows.sort(key=lambda r: (r[0].lower(), r[1].lower()))
        
        # Atomic write: write to temp file then rename
        temp_filename = f"{filename}.tmp"
        with open(temp_filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter=",", quoting=csv.QUOTE_MINIMAL)
            writer.writerow(["Company", "Email", "Phone", "Website"])
            writer.writerows(rows)
        os.replace(temp_filename, filename)
        
        print(f"\n[+] {len(rows)} unique business emails saved in: {filename}")
        return filename

    @staticmethod
    def _guess_company_name(email: str, source_url: str) -> str:
        try:
            parsed_url = urlparse(source_url)
            domain = parsed_url.netloc.replace("www.", "")
            parts = domain.split(".")
            base = parts[-2] if len(parts) >= 2 else parts[0]
        except Exception:
            try:
                domain = email.split("@", 1)[1]
                base = domain.split(".")[0]
            except Exception:
                base = "Unknown"
        base = base.replace("-", " ").replace("_", " ")
        return " ".join(word.capitalize() for word in base.split())

    def close(self) -> None:
        try:
            if self.context is not None:
                self.context.close()
            if self.browser is not None:
                self.browser.close()
            if self.pw is not None:
                self.pw.stop()
        except Exception:
            pass


def main() -> None:
    print("=" * 70)
    print("   EMAIL SCRAPER")
    print("   Google Maps Scraping with Browser Automation")
    print("=" * 70)
    default_cfg = os.environ.get("SCRAPER_CONFIG", "config.json")
    parser = argparse.ArgumentParser(description="Email Scraper (Google Maps)")
    parser.add_argument("--config", default=default_cfg, help="Path to config JSON file")
    args = parser.parse_args()
    try:
        cfg = Config.load(args.config)
    except FileNotFoundError:
        print(f"[X] Config file not found: {args.config}")
        return
    except Exception as e:
        print(f"[X] Failed to load config: {e}")
        return
    if not cfg.search_term:
        print("[X] 'search_term' is required in config.json")
        return
    if not cfg.locations:
        print("[X] 'locations' must be a non-empty array in config.json")
        return
    queries = [f"{cfg.search_term} {city}" for city in cfg.locations]
    print("\n[*] Configuration:")
    print(f"   - Headless: {cfg.headless}")
    print(f"   - Threading: {cfg.use_threading} (workers={cfg.max_thread_workers})")
    print(f"   - Scroll pause: {cfg.scroll_pause_time}s, Max scrolls: {cfg.max_scroll_attempts}")
    scraper: Optional[EmailScraper] = None
    try:
        scraper = EmailScraper(cfg)
        print("\n[*] Starting search...")
        print("[!] This may take several minutes...\n")
        scraper.run(
            queries=queries,
            max_sites_per_query=cfg.max_results_per_query,
            scroll_pause=cfg.scroll_pause_time,
            max_scrolls=cfg.max_scroll_attempts,
        )
        if scraper.emails:
            filename = scraper.save_results()
            print(f"\n{'=' * 70}")
            print("[+] DONE!")
            print(f"{'=' * 70}")
            print("\n[>] Summary:")
            print(f"   [>] Visited websites: {len(scraper.visited_urls)}")
            print(f"   [>] Found emails: {len(scraper.emails)}")
            print(f"   [>] Saved in: {filename}")
        else:
            print("\n[!] No emails found")
    except Exception as e:
        print(f"\n[X] Error: {str(e)}")
    finally:
        if scraper:
            scraper.close()
            print("\n[*] Browser closed")


if __name__ == "__main__":
    main()
