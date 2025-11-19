from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
import re
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from urllib.parse import urlparse
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright


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
    max_concurrent_pages: int = 5
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
            max_concurrent_pages=int(data.get("max_concurrent_pages", data.get("max_thread_workers", 5))),
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
        self.headless = config.headless
        self.output_filename = Path(config.output_filename)
        self.phone_min_digits = config.phone_min_digits
        self.pw: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self._load_existing_data()

    def _load_existing_data(self) -> None:
        if not self.output_filename.exists():
            return
            
        print(f"[*] Loading existing data from {self.output_filename}...")
        try:
            with self.output_filename.open("r", encoding="utf-8") as f:
                reader = csv.reader(f)
                next(reader, None) # Skip header
                count = 0
                for row in reader:
                    if len(row) < 4:
                        continue
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
        except Exception as exc:
            print(f"[!] Failed to load existing data: {exc}")

    async def _init_playwright(self) -> None:
        self.pw = await async_playwright().start()
        launch_args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--disable-notifications",
            "--disable-popup-blocking",
        ]
        try:
            self.browser = await self.pw.chromium.launch(headless=self.headless, args=launch_args)
        except Exception:
             # Attempt auto-install then retry once
            import subprocess
            try:
                subprocess.run(["python", "-m", "playwright", "install", "--with-deps", "chromium"], check=True)
            except Exception:
                pass
            self.browser = await self.pw.chromium.launch(headless=self.headless, args=launch_args)

    async def _create_context(self) -> BrowserContext:
        if not self.browser:
            raise RuntimeError("Browser not initialized")
        
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        context = await self.browser.new_context(user_agent=ua, java_script_enabled=True)
        # Optimize: Block resources to speed up scraping
        await context.route("**/*.{png,jpg,jpeg,gif,webp,svg,css,woff,woff2,mp4,mp3}", lambda route: route.abort())
        return context

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

    async def scrape_google_maps(self, query: str, max_results: int = 0) -> List[str]:
        print(f"\n[*] Google Maps search: '{query}'")
        websites: List[str] = []
        all_result_urls: Set[str] = set()
        
        context = await self._create_context()
        page = await context.new_page()
        
        try:
            search_url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}"
            await page.goto(search_url, wait_until="domcontentloaded")
            await self._accept_gmaps_cookies(page)
            print("[*] Waiting for search results...")
            await asyncio.sleep(4)
            await self._accept_gmaps_cookies(page)
            
            # Scrolling logic
            result_selector = None
            for selector in GOOGLE_MAPS_RESULT_SELECTORS:
                try:
                    if await page.query_selector(selector):
                        result_selector = selector
                        break
                except Exception:
                    continue
            
            if not result_selector:
                print("[!] Could not find results panel")
                await context.close()
                return websites
            
            print("[*] Scraping results...")
            last_count = 0
            scroll_attempts = 0
            no_new_results_count = 0
            max_scrolls = self.config.max_scroll_attempts
            
            while scroll_attempts < max_scrolls:
                try:
                    result_links = await page.query_selector_all(result_selector)
                    current_urls = set()
                    for el in result_links:
                        href = await el.get_attribute("href")
                        if href and "/maps/place/" in href:
                            current_urls.add(href)
                    
                    all_result_urls.update(current_urls)
                    current_count = len(all_result_urls)
                    
                    if current_count > last_count:
                        print(f"   [>] Found {current_count} results so far...")
                        last_count = current_count
                        no_new_results_count = 0
                    else:
                        no_new_results_count += 1
                        if no_new_results_count >= 3:
                            break
                            
                    panel = await page.query_selector("div[role='feed']")
                    if panel:
                        await panel.evaluate("el => el.scrollTop = el.scrollHeight")
                    await asyncio.sleep(self.config.scroll_pause_time)
                    scroll_attempts += 1
                except Exception:
                    break

            result_urls = list(all_result_urls)
            if max_results > 0:
                result_urls = result_urls[:max_results]
            
            print(f"[+] Collected {len(result_urls)} unique results. Visiting details...")
            
            # Visit each map result to get the website URL
            # We can do this concurrently too, but let's keep it simple: serially for maps details, parallel for external sites
            for i, place_url in enumerate(result_urls, 1):
                try:
                    await page.goto(place_url, wait_until="domcontentloaded")
                    await asyncio.sleep(1) # Slight delay to load content
                    content = await page.content()
                    
                    # Opportunistic email/phone grab from Maps entry
                    maps_emails = self.extract_emails(content)
                    maps_phone = self.extract_phone(content)
                    if maps_emails:
                        self._record_contact_details(place_url, maps_emails, maps_phone)

                    website_pattern = (
                        r'https?://(?!.*google\.com|.*facebook\.com|.*instagram\.com)'
                        r"[a-zA-Z0-9-]+\.[a-zA-Z]{2,}(?:/[^\s\"<>]*)?"
                    )
                    matches = re.findall(website_pattern, content)
                    website_url = next(
                        (m for m in matches if not any(s in m.lower() for s in MAPS_WEBSITE_SKIP_KEYWORDS)),
                        None
                    )
                    
                    if not website_url:
                        try:
                            wb = await page.query_selector("a[data-item-id='authority']")
                            website_url = await wb.get_attribute("href") if wb else None
                        except Exception:
                            pass
                            
                    if website_url:
                        if "/url?q=" in website_url:
                            website_url = website_url.split("/url?q=", 1)[1].split("&")[0]
                        if any(s in website_url.lower() for s in SOCIAL_DOMAINS_TO_SKIP):
                            continue
                        clean_url = website_url.split("?", 1)[0].split("#", 1)[0]
                        if clean_url not in websites:
                            websites.append(clean_url)
                except Exception:
                    continue
                    
        except Exception as e:
            print(f"[X] Error with Google Maps: {e}")
        finally:
            await context.close()
            
        return websites

    async def _accept_gmaps_cookies(self, page: Page) -> None:
        selectors = [
            "button[aria-label='Accept all']",
            "button[aria-label='I agree']",
            "button[aria-label='Accept all cookies']",
            "button[jsname='b3VHJd']",
        ]
        texts = ["Accept all", "I agree", "Accept cookies", "Agree to all"]
        
        async def try_click(target) -> bool:
            for sel in selectors:
                try:
                    el = await target.query_selector(sel)
                    if el:
                        await el.click()
                        return True
                except Exception:
                    pass
            for text in texts:
                try:
                    el = await target.query_selector(f"//button[contains(., '{text}')]")
                    if el:
                        await el.click()
                        return True
                except Exception:
                    pass
            return False

        try:
            if await try_click(page):
                return
            frames = page.frames
            for frame in frames:
                if await try_click(frame):
                    return
        except Exception:
            pass

    def _record_contact_details(self, url: str, emails: List[str], phone: Optional[str]) -> None:
        # Synchronous part since we just update dicts
        for email in emails:
            email_lower = email.lower().strip()
            if any(e.lower() == email_lower for e in self.emails.keys()):
                continue
            self.emails[email.strip()] = url
            if phone and url not in self.company_phones:
                self.company_phones[url] = phone
            print(f"   [+] Email found: {email.strip()}")

    async def scrape_website_concurrent(self, url: str, semaphore: asyncio.Semaphore) -> None:
        if url in self.visited_urls:
            return
        self.visited_urls.add(url)
        
        async with semaphore:
            context = await self._create_context()
            page = await context.new_page()
            try:
                print(f"[*] Scanning: {url}")
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(2)
                except Exception:
                    # Retry once or just fail fast
                    await context.close()
                    return

                content = await page.content()
                emails = self.extract_emails(content)
                phone = self.extract_phone(content)
                
                if emails:
                    self._record_contact_details(url, emails, phone)
                else:
                    # Try contact page
                    for keyword in CONTACT_KEYWORDS:
                        try:
                            link = await page.query_selector(f"a:has-text('{keyword}')")
                            if not link:
                                continue
                            href = await link.get_attribute("href")
                            if not href or not href.startswith("http"):
                                continue
                            
                            if href in self.visited_urls:
                                continue
                            self.visited_urls.add(href)
                            
                            await page.goto(href, wait_until="domcontentloaded", timeout=20000)
                            await asyncio.sleep(1)
                            content = await page.content()
                            contact_emails = self.extract_emails(content)
                            if contact_emails:
                                self._record_contact_details(href, contact_emails, None)
                            break
                        except Exception:
                            continue
                        
            except Exception:
                pass # Silent fail on individual sites to keep moving
            finally:
                await context.close()

    async def run(self) -> None:
        queries = [f"{self.config.search_term} {city}" for city in self.config.locations]
        print(f"\n[>] Collecting websites from {len(queries)} search queries...")
        
        await self._init_playwright()
        if not self.browser:
             print("[X] Browser failed to launch")
             return

        all_websites: List[str] = []
        for i, query in enumerate(queries, 1):
            print(f"\n{'=' * 60}")
            print(f"Search Query {i}/{len(queries)}: {query}")
            print(f"{'=' * 60}")
            sites = await self.scrape_google_maps(query, self.config.max_results_per_query)
            all_websites.extend(sites)
            if i < len(queries):
                delay = random.uniform(self.config.delay_between_queries_seconds_min, self.config.delay_between_queries_seconds_max)
                print(f"\n[*] Waiting {delay:.1f} seconds...")
                await asyncio.sleep(delay)
        
        unique_websites = list(set(all_websites))
        print(f"\n{'=' * 60}")
        print(f"[>] TOTAL: {len(unique_websites)} unique websites found")
        print("[>] Starting email extraction with concurrency...")
        print(f"{'=' * 60}")

        # Parallel processing of websites
        sem = asyncio.Semaphore(self.config.max_concurrent_pages)
        tasks = []
        
        for i, url in enumerate(unique_websites):
            tasks.append(self.scrape_website_concurrent(url, sem))
            
        # Chunking tasks to allow incremental saves
        chunk_size = 10
        for i in range(0, len(tasks), chunk_size):
            chunk = tasks[i:i + chunk_size]
            await asyncio.gather(*chunk)
            self.save_results()
            print(f"\n[>] Progress: {len(self.emails)} emails found so far")
            
        self.save_results()
        print(f"\n{'=' * 70}")
        print("[+] DONE!")
        print(f"{'=' * 70}")

    def save_results(self, filename: Optional[str] = None) -> str:
        if filename is None:
            filename = str(self.output_filename)
        else:
            filename = str(filename)
            
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
        
        rows = [[d["company"], d["email"], d["phone"], d["website"]] for d in unique_emails.values()]
        rows.sort(key=lambda r: (r[0].lower(), r[1].lower()))
        
        temp_filename = f"{filename}.tmp"
        with open(temp_filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter=",", quoting=csv.QUOTE_MINIMAL)
            writer.writerow(["Company", "Email", "Phone", "Website"])
            writer.writerows(rows)
        os.replace(temp_filename, filename)
        return str(filename)

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

    async def close(self) -> None:
        if self.browser:
            await self.browser.close()
        if self.pw:
            await self.pw.stop()


async def main_async() -> None:
    print("=" * 70)
    print("   EMAIL SCRAPER (Async)")
    print("=" * 70)
    default_cfg = os.environ.get("SCRAPER_CONFIG", "config.json")
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=default_cfg)
    args = parser.parse_args()
    
    try:
        cfg = Config.load(args.config)
    except Exception as e:
        print(f"[X] Config Error: {e}")
        return

    if not cfg.search_term:
        print("[X] 'search_term' required")
        return

    scraper = EmailScraper(cfg)
    try:
        await scraper.run()
    finally:
        await scraper.close()

if __name__ == "__main__":
    asyncio.run(main_async())
