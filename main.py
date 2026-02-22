import asyncio
import csv
import json
import logging
import os
import re
import threading
from collections import deque
from pathlib import Path
from urllib.parse import quote_plus

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from playwright.async_api import async_playwright

BASE_DIR = Path(__file__).resolve().parent
DB_FILE  = BASE_DIR / "contacts.csv"
CFG_FILE = BASE_DIR / "config.json"

DEFAULT_CFG = {
    "search_terms": "Construction", "locations": "Thessaloniki",
    "headless": True, "max_results": 10, "concurrency": 10,
}

FIELDS          = ["Company", "Email", "Phone", "Website", "Category", "Address", "Rating", "Reviews", "Maps URL"]
EXCLUDED_DOMAINS = ["google.com", "facebook.com", "instagram.com"]
EMAIL_RE        = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE        = re.compile(r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")


class MemoryHandler(logging.Handler):
    def __init__(self, capacity=100):
        super().__init__()
        self.buffer = deque(maxlen=capacity)

    def emit(self, record):
        self.buffer.append(self.format(record))


log_handler = MemoryHandler()
log = logging.getLogger("scraper")
log.setLevel(logging.INFO)
for h in [log_handler, logging.FileHandler(BASE_DIR / "scraper.log"), logging.StreamHandler()]:
    log.addHandler(h)
logging.getLogger("uvicorn.access").setLevel(logging.ERROR)


class Engine:
    def __init__(self):
        self.active = False
        self.data   = []
        self._lock  = threading.Lock()
        if DB_FILE.exists():
            with open(DB_FILE, encoding="utf-8") as f:
                self.data = list(csv.DictReader(f))

    def save(self):
        tmp = Path(f"{DB_FILE}.tmp")
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            with self._lock:
                w.writerows(self.data)
        tmp.replace(DB_FILE)

    async def run(self, cfg):
        self.active = True
        log.info("Starting scraper...")
        queries = [
            f"{t.strip()} {loc.strip()}"
            for t   in cfg["search_terms"].split(",") if t.strip()
            for loc in cfg["locations"].split(",")    if loc.strip()
        ]
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=cfg["headless"])
            for q in queries:
                if not self.active:
                    break
                await self._scrape_maps(browser, q, int(cfg.get("max_results", 10)))

            with self._lock:
                sites = [r for r in self.data if r.get("Website") and not r.get("Email")]
            if sites and self.active:
                log.info(f"Enriching {len(sites)} websites...")
                sem = asyncio.Semaphore(cfg.get("concurrency", 10))
                await asyncio.gather(*[self._scrape_site(browser, r, sem) for r in sites])

            await browser.close()
        self.active = False
        log.info("Job finished.")

    async def _scrape_maps(self, browser, q, limit):
        async def text(page, sel):
            try:
                return await page.inner_text(sel, timeout=3000)
            except Exception:
                return ""

        ctx  = await browser.new_context(viewport={"width": 1200, "height": 800})
        page = await ctx.new_page()
        try:
            log.info(f"Searching: {q}")
            await page.goto(f"https://www.google.com/maps/search/{quote_plus(q)}", wait_until="domcontentloaded")

            try:
                await page.locator(
                    "button[aria-label*='Accept'], button[aria-label*='agree'], button[aria-label*='Αποδοχή']"
                ).first.click(timeout=3000)
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
                    if limit > 0 and len(found) >= limit:
                        break
                links = await page.query_selector_all("a.hfpxzc")
                urls  = [href for link in links if (href := await link.get_attribute("href"))]
                if limit > 0:
                    urls = urls[:limit]

            log.info(f"Processing {len(urls)} listings...")
            changed = False
            for url in urls:
                if not self.active:
                    break
                with self._lock:
                    if any(r.get("Maps URL") == url for r in self.data):
                        continue

                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_selector("h1.DUwDvf", timeout=5000)

                res = {
                    "Company":  await text(page, "h1.DUwDvf"),
                    "Category": await text(page, "button.DkEaL"),
                    "Address":  (await text(page, "button[data-item-id='address']")).strip(),
                    "Phone":    (await text(page, "button[data-item-id*='phone:tel:']")).strip(),
                    "Website":  "",
                    "Email":    "",
                    "Rating":   await text(page, "div.F7nice span span[aria-hidden='true']"),
                    "Reviews":  (await text(page, "div.F7nice span[aria-label*='reviews']")).strip("()"),
                    "Maps URL": url,
                }

                wb = await page.query_selector("a[data-item-id='authority']")
                if wb and (href := await wb.get_attribute("href")):
                    if not any(d in href.lower() for d in EXCLUDED_DOMAINS):
                        res["Website"] = href.split("?")[0].rstrip("/")

                with self._lock:
                    self.data.append(res)
                changed = True
                log.info(f"Captured: {res['Company']}")

            if changed:
                self.save()
        finally:
            await ctx.close()

    async def _scrape_site(self, browser, res, sem):
        async with sem:
            if not self.active:
                return
            ctx  = await browser.new_context()
            page = await ctx.new_page()
            await ctx.route("**/*.{png,jpg,jpeg,gif,webp,svg,css,woff,woff2}", lambda r: r.abort())
            try:
                await page.goto(res["Website"], timeout=15000)
                html = await page.content()
                if m := EMAIL_RE.search(html):
                    res["Email"] = m.group(0).lower()
                if not res["Phone"] and (m := PHONE_RE.search(html)):
                    res["Phone"] = m.group(0)
                self.save()
            except Exception:
                pass
            finally:
                await ctx.close()


engine    = Engine()
app       = FastAPI()
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def load_cfg() -> dict:
    return json.loads(CFG_FILE.read_text()) if CFG_FILE.exists() else DEFAULT_CFG


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/status")
async def status():
    with engine._lock:
        leads = list(engine.data)
    return {"running": engine.active, "leads": leads, "logs": list(log_handler.buffer), "config": load_cfg()}


@app.post("/control/{action}")
async def control(action: str):
    if action == "start" and not engine.active:
        asyncio.create_task(engine.run(load_cfg()))
    elif action == "stop":
        engine.active = False
    elif action == "clear":
        with engine._lock:
            engine.data = []
        DB_FILE.unlink(missing_ok=True)
        log.info("Results cleared.")
    else:
        return JSONResponse({"error": f"Unknown action: {action}"}, status_code=400)
    return {"success": True}


@app.post("/config")
async def save_config(request: Request):
    cfg = await request.json()
    if not isinstance(cfg, dict):
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
    CFG_FILE.write_text(json.dumps(cfg))
    return {"success": True}


@app.get("/download")
async def download():
    if not DB_FILE.exists():
        return JSONResponse({"error": "No data yet"}, status_code=404)
    return FileResponse(DB_FILE, filename="contacts.csv", media_type="text/csv")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
