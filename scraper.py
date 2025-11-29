"""Email scraper using Playwright to scrape Google Maps and company websites."""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import random
import re
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from shared import (
    CONTACT_KEYWORDS,
    EMAIL_REGEX,
    INVALID_EMAIL_PATTERNS,
    MAPS_RESULT_SELECTORS,
    PHONE_PATTERNS,
    SKIP_DOMAINS,
    Config,
)

BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--disable-notifications",
    "--disable-popup-blocking",
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class EmailScraper:
    """Scrapes emails from Google Maps results and company websites."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.output_filename = Path(config.output_filename)
        self.emails: dict[str, str] = {}
        self.phones: dict[str, str] = {}
        self.visited: set[str] = set()
        self.pw: Playwright | None = None
        self.browser: Browser | None = None
        self._load_existing()

    def _load_existing(self) -> None:
        """Load previously scraped data to resume."""
        if not self.output_filename.exists():
            return
        try:
            with self.output_filename.open() as f:
                for row in csv.DictReader(f):
                    if email := row.get("Email"):
                        self.emails[email] = row.get("Website", "")
                    if (website := row.get("Website")) and (phone := row.get("Phone")):
                        self.phones[website] = phone
                        self.visited.add(website)
            print(f"[*] Loaded {len(self.emails)} existing records")
        except Exception as e:
            print(f"[!] Failed to load existing data: {e}")

    async def start(self) -> None:
        """Initialize browser."""
        self.pw = await async_playwright().start()
        try:
            self.browser = await self.pw.chromium.launch(headless=self.config.headless, args=BROWSER_ARGS)
        except Exception:
            import subprocess
            subprocess.run(["python", "-m", "playwright", "install", "--with-deps", "chromium"], check=True)
            self.browser = await self.pw.chromium.launch(headless=self.config.headless, args=BROWSER_ARGS)

    async def stop(self) -> None:
        """Cleanup browser."""
        if self.browser:
            await self.browser.close()
        if self.pw:
            await self.pw.stop()

    async def _new_context(self) -> BrowserContext:
        """Create optimized browser context."""
        ctx = await self.browser.new_context(user_agent=USER_AGENT)
        await ctx.route("**/*.{png,jpg,jpeg,gif,webp,svg,css,woff,woff2,mp4,mp3}", lambda r: r.abort())
        return ctx

    def _extract_emails(self, text: str) -> list[str]:
        """Extract valid emails from text."""
        return [
            e for e in re.findall(EMAIL_REGEX, text, re.IGNORECASE)
            if not any(p in e.lower() for p in INVALID_EMAIL_PATTERNS)
        ]

    def _extract_phone(self, text: str) -> str | None:
        """Extract first valid phone number."""
        for pattern in PHONE_PATTERNS:
            for match in re.findall(pattern, text):
                digits = re.sub(r"\D", "", match)
                if self.config.phone_min_digits <= len(digits) <= 15 and len(set(digits)) > 1:
                    return match
        return None

    def _record(self, url: str, emails: list[str], phone: str | None = None) -> None:
        """Record contact details."""
        for email in emails:
            if email.lower() not in (e.lower() for e in self.emails):
                self.emails[email] = url
                print(f"   [+] {email}")
        if phone and url not in self.phones:
            self.phones[url] = phone

    async def _accept_cookies(self, page: Page) -> None:
        """Dismiss cookie dialogs."""
        for selector in ["button[aria-label='Accept all']", "button[jsname='b3VHJd']"]:
            try:
                if el := await page.query_selector(selector):
                    await el.click()
                    return
            except Exception:
                pass

    async def scrape_maps(self, query: str, max_results: int = 0) -> list[str]:
        """Scrape business websites from Google Maps."""
        print(f"\n[*] Maps search: '{query}'")
        ctx = await self._new_context()
        page = await ctx.new_page()
        websites = []

        try:
            await page.goto(f"https://www.google.com/maps/search/{query.replace(' ', '+')}", wait_until="domcontentloaded")
            await self._accept_cookies(page)
            await asyncio.sleep(3)

            # Find working selector
            selector = next((s for s in MAPS_RESULT_SELECTORS if await page.query_selector(s)), None)
            if not selector:
                print("[!] No results found")
                return websites

            # Scroll and collect results
            urls: set[str] = set()
            stale_count = 0
            for _ in range(self.config.max_scroll_attempts):
                links = await page.query_selector_all(selector)
                new_urls = {await el.get_attribute("href") for el in links if await el.get_attribute("href") and "/maps/place/" in (await el.get_attribute("href") or "")}
                if new_urls - urls:
                    urls.update(new_urls)
                    stale_count = 0
                else:
                    stale_count += 1
                    if stale_count >= 3:
                        break
                if panel := await page.query_selector("div[role='feed']"):
                    await panel.evaluate("el => el.scrollTop = el.scrollHeight")
                await asyncio.sleep(self.config.scroll_pause_time)

            result_urls = list(urls)[:max_results] if max_results else list(urls)
            print(f"[+] Found {len(result_urls)} results")

            # Extract websites from each result
            for url in result_urls:
                try:
                    await page.goto(url, wait_until="domcontentloaded")
                    await asyncio.sleep(0.5)
                    content = await page.content()

                    if emails := self._extract_emails(content):
                        self._record(url, emails, self._extract_phone(content))

                    # Find website link
                    website = None
                    if wb := await page.query_selector("a[data-item-id='authority']"):
                        website = await wb.get_attribute("href")
                    if website and not any(d in website.lower() for d in SKIP_DOMAINS):
                        clean = website.split("?")[0].split("#")[0]
                        if clean not in websites:
                            websites.append(clean)
                except Exception:
                    continue

        except Exception as e:
            print(f"[X] Maps error: {e}")
        finally:
            await ctx.close()

        return websites

    async def scrape_website(self, url: str, sem: asyncio.Semaphore) -> None:
        """Scrape a website for contact info."""
        if url in self.visited:
            return
        self.visited.add(url)

        async with sem:
            ctx = await self._new_context()
            page = await ctx.new_page()
            try:
                print(f"[*] {url}")
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(1)

                content = await page.content()
                if emails := self._extract_emails(content):
                    self._record(url, emails, self._extract_phone(content))
                else:
                    # Try contact page
                    for keyword in CONTACT_KEYWORDS:
                        try:
                            link = await page.query_selector(f"a:has-text('{keyword}')")
                            if link and (href := await link.get_attribute("href")) and href.startswith("http") and href not in self.visited:
                                self.visited.add(href)
                                await page.goto(href, wait_until="domcontentloaded", timeout=20000)
                                if emails := self._extract_emails(await page.content()):
                                    self._record(href, emails)
                                break
                        except Exception:
                            continue
            except Exception:
                pass
            finally:
                await ctx.close()

    def save(self) -> None:
        """Save results to CSV."""
        rows = {}
        for email, url in self.emails.items():
            key = email.lower()
            if key not in rows:
                domain = urlparse(url).netloc.replace("www.", "").split(".")[0] if url else "unknown"
                company = " ".join(w.capitalize() for w in domain.replace("-", " ").replace("_", " ").split())
                rows[key] = [company, email, self.phones.get(url, ""), url]

        sorted_rows = sorted(rows.values(), key=lambda r: (r[0].lower(), r[1].lower()))
        tmp = f"{self.output_filename}.tmp"
        with open(tmp, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Company", "Email", "Phone", "Website"])
            w.writerows(sorted_rows)
        os.replace(tmp, self.output_filename)

    async def run(self) -> None:
        """Main scraping routine."""
        queries = [f"{self.config.search_term} {loc}" for loc in self.config.locations]
        print(f"\n[>] Running {len(queries)} queries...")

        await self.start()
        if not self.browser:
            return

        websites: list[str] = []
        for i, query in enumerate(queries, 1):
            print(f"\n{'=' * 50}\nQuery {i}/{len(queries)}: {query}\n{'=' * 50}")
            websites.extend(await self.scrape_maps(query, self.config.max_results_per_query))
            if i < len(queries):
                delay = random.uniform(*self.config.delay_between_queries)
                await asyncio.sleep(delay)

        unique = list(set(websites))
        print(f"\n[>] Scanning {len(unique)} websites...")

        sem = asyncio.Semaphore(self.config.max_concurrent_pages)
        for i in range(0, len(unique), 10):
            await asyncio.gather(*[self.scrape_website(u, sem) for u in unique[i:i+10]])
            self.save()
            print(f"[>] Progress: {len(self.emails)} emails")

        self.save()
        print(f"\n[+] Done! {len(self.emails)} emails saved.")
        await self.stop()


async def main() -> None:
    print("=" * 50)
    print("   EMAIL SCRAPER")
    print("=" * 50)

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=os.environ.get("SCRAPER_CONFIG", "config.json"))
    args = parser.parse_args()

    config = Config.load(args.config)
    if not config.search_term:
        print("[X] search_term required in config")
        return

    scraper = EmailScraper(config)
    try:
        await scraper.run()
    finally:
        await scraper.stop()


if __name__ == "__main__":
    asyncio.run(main())
