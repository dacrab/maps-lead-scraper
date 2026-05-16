import asyncio
import csv
import json
import logging
import os
import re
from collections import deque
from pathlib import Path
from urllib.parse import quote_plus

import aiohttp
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from playwright.async_api import async_playwright

BASE_DIR = Path(__file__).resolve().parent
DB_FILE = BASE_DIR / "contacts.csv"
CFG_FILE = BASE_DIR / "config.json"

DEFAULT_CFG = {
    "search_terms": "", "locations": "",
    "headless": True, "max_results": 10, "concurrency": 5,
}

FIELDS = ["Company", "Email", "Phone", "Website", "Category", "Address", "Rating", "Reviews", "Maps URL"]
EXCLUDED_DOMAINS = {"google.com", "facebook.com", "instagram.com"}
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}\b")
CONSENT_SELECTORS = "button[aria-label*='Accept'], button[aria-label*='agree'], button[aria-label*='Αποδοχή']"
CONTACT_PATHS = ("contact", "about", "contact-us", "kontakt", "epikoinonia")

CHROMIUM_ARGS = [
    "--disable-gpu", "--disable-dev-shm-usage", "--disable-extensions",
    "--no-sandbox", "--disable-background-networking", "--disable-default-apps",
    "--disable-sync", "--disable-translate", "--metrics-recording-only",
    "--no-first-run", "--single-process", "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding", "--disable-component-update",
]


def load_config():
    if CFG_FILE.exists():
        return json.loads(CFG_FILE.read_text())
    return dict(DEFAULT_CFG)


class MemoryHandler(logging.Handler):
    def __init__(self, capacity=50):
        super().__init__()
        self.buffer = deque(maxlen=capacity)

    def emit(self, record):
        self.buffer.append(self.format(record))


log_handler = MemoryHandler()
log = logging.getLogger("scraper")
log.setLevel(logging.INFO)
log.addHandler(log_handler)
log.addHandler(logging.FileHandler(BASE_DIR / "scraper.log"))
logging.getLogger("uvicorn.access").setLevel(logging.ERROR)


class Engine:
    def __init__(self):
        self.active = False
        self._seen_urls: set[str] = set()
        self._lock = asyncio.Lock()
        self._cache: list[dict] = []
        self._cache_mtime: float = 0
        if DB_FILE.exists():
            with open(DB_FILE, encoding="utf-8") as f:
                self._seen_urls = {r.get("Maps URL", "") for r in csv.DictReader(f)}

    def _read_leads(self):
        if not DB_FILE.exists():
            return []
        with open(DB_FILE, encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def _read_leads_cached(self):
        if not DB_FILE.exists():
            self._cache, self._cache_mtime = [], 0
            return []
        mtime = DB_FILE.stat().st_mtime
        if mtime != self._cache_mtime:
            self._cache = self._read_leads()
            self._cache_mtime = mtime
        return self._cache

    def _append(self, row: dict):
        exists = DB_FILE.exists()
        with open(DB_FILE, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if not exists:
                w.writeheader()
            w.writerow(row)

    def _rewrite(self, rows: list[dict]):
        tmp = DB_FILE.with_suffix(".tmp")
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
        tmp.replace(DB_FILE)

    async def run(self, cfg):
        self.active = True
        try:
            await self._run(cfg)
        finally:
            self.active = False

    async def _run(self, cfg):
        log.info("Starting scraper...")
        queries = [
            f"{t.strip()} {loc.strip()}"
            for t in cfg["search_terms"].split(",") if t.strip()
            for loc in cfg["locations"].split(",") if loc.strip()
        ]
        if not queries:
            log.info("No search queries configured.")
            return

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=cfg["headless"], args=CHROMIUM_ARGS)
            for q in queries:
                if not self.active:
                    break
                await self._scrape_maps(browser, q, int(cfg.get("max_results", 10)))
            await browser.close()

        if not self.active:
            return

        async with self._lock:
            sites = [r for r in self._read_leads() if r.get("Website") and not r.get("Email")]
        if not sites:
            log.info("Done.")
            return

        log.info(f"Enriching {len(sites)} websites...")
        sem = asyncio.Semaphore(min(int(cfg.get("concurrency", 5)), 10))
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=8),
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
        ) as session:
            await asyncio.gather(*[self._enrich(session, res, sem) for res in sites])

        async with self._lock:
            all_leads = self._read_leads()
            enriched = {r["Website"]: r for r in sites if r.get("Email")}
            for lead in all_leads:
                if lead.get("Website") in enriched:
                    lead["Email"] = enriched[lead["Website"]]["Email"]
            self._rewrite(all_leads)
        log.info("Done.")

    async def _scrape_maps(self, browser, q, limit):
        async def text(sel):
            try:
                return (await page.inner_text(sel, timeout=3000)).strip()
            except Exception:
                return ""

        ctx = await browser.new_context(viewport={"width": 1200, "height": 800})
        page = await ctx.new_page()
        try:
            log.info(f"Searching: {q}")
            try:
                await page.goto(f"https://www.google.com/maps/search/{quote_plus(q)}", wait_until="domcontentloaded")
            except Exception as e:
                log.warning(f"Failed to load Maps for '{q}': {e}")
                return

            try:
                await page.locator(CONSENT_SELECTORS).first.click(timeout=3000)
            except Exception:
                pass

            if "/maps/place/" in page.url:
                urls = [page.url]
            else:
                last = 0
                for _ in range(20):
                    await page.mouse.wheel(0, 4000)
                    await asyncio.sleep(1.5)
                    found = await page.query_selector_all("a.hfpxzc")
                    if len(found) == last:
                        break
                    last = len(found)
                    if limit and len(found) >= limit:
                        break
                links = await page.query_selector_all("a.hfpxzc")
                urls = [href for link in links if (href := await link.get_attribute("href"))]
                if limit:
                    urls = urls[:limit]

            log.info(f"Processing {len(urls)} listings...")
            for url in urls:
                if not self.active:
                    break
                async with self._lock:
                    if url in self._seen_urls:
                        continue
                    self._seen_urls.add(url)

                try:
                    await page.goto(url, wait_until="domcontentloaded")
                    await page.wait_for_selector("h1.DUwDvf", timeout=5000)
                except Exception as e:
                    log.warning(f"Failed to load listing: {e}")
                    continue

                res = {
                    "Company": await text("h1.DUwDvf"),
                    "Category": await text("button.DkEaL"),
                    "Address": await text("button[data-item-id='address']"),
                    "Phone": await text("button[data-item-id*='phone:tel:']"),
                    "Website": "",
                    "Email": "",
                    "Rating": await text("div.F7nice span span[aria-hidden='true']"),
                    "Reviews": (await text("div.F7nice span[aria-label*='reviews']")).strip("()"),
                    "Maps URL": url,
                }

                wb = await page.query_selector("a[data-item-id='authority']")
                if wb and (href := await wb.get_attribute("href")):
                    if not any(d in href.lower() for d in EXCLUDED_DOMAINS):
                        res["Website"] = href.split("?")[0].rstrip("/")

                async with self._lock:
                    self._append(res)
                log.info(f"Captured: {res['Company']}")
        finally:
            await ctx.close()

    async def _enrich(self, session, res, sem):
        async with sem:
            if not self.active:
                return
            base = res["Website"]
            urls = [base] + [f"{base.rstrip('/')}/{p}" for p in CONTACT_PATHS]
            for url in urls:
                try:
                    async with session.get(url, ssl=False, allow_redirects=True) as resp:
                        if resp.status != 200:
                            continue
                        html = await resp.text(errors="ignore")
                    if m := EMAIL_RE.search(html):
                        res["Email"] = m.group(0).lower()
                        break
                except Exception:
                    continue
            log.info(f"Enriched: {res.get('Company', base)} {'✓' if res.get('Email') else '—'}")


engine = Engine()
app = FastAPI()
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/api/status")
async def status():
    async with engine._lock:
        leads = engine._read_leads_cached()
    return {"running": engine.active, "leads": leads, "logs": list(log_handler.buffer), "config": load_config()}


@app.post("/control/{action}")
async def control(action: str):
    if action == "start" and not engine.active:
        task = asyncio.create_task(engine.run(load_config()))
        task.add_done_callback(lambda t: log.error(f"Scraper crashed: {t.exception()}") if t.exception() else None)
    elif action == "stop":
        engine.active = False
    elif action == "clear":
        async with engine._lock:
            engine._seen_urls.clear()
        DB_FILE.unlink(missing_ok=True)
        log_handler.buffer.clear()
        log.info("Results cleared.")
    else:
        return JSONResponse({"error": f"Unknown action: {action}"}, status_code=400)
    return {"success": True}


@app.post("/config")
async def save_config(request: Request):
    cfg = await request.json()
    if not isinstance(cfg, dict):
        return JSONResponse({"error": "Invalid"}, status_code=400)
    cleaned = {k: cfg[k] for k in DEFAULT_CFG if k in cfg}
    if not cleaned:
        return JSONResponse({"error": "Invalid"}, status_code=400)
    CFG_FILE.write_text(json.dumps(cleaned))
    return {"success": True}


@app.get("/download")
async def download():
    if not DB_FILE.exists():
        return JSONResponse({"error": "No data yet"}, status_code=404)
    return FileResponse(DB_FILE, filename="contacts.csv", media_type="text/csv")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", 8000)))
