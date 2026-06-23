import asyncio
import csv
import json
import logging
import os
import re
from collections import deque
from pathlib import Path

import aiohttp
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
DB_FILE = BASE_DIR / "contacts.csv"
CFG_FILE = BASE_DIR / "config.json"

DEFAULT_CFG = {
    "search_terms": "", "locations": "",
    "api_key": "", "max_results": 20, "concurrency": 5,
}

FIELDS = ["Company", "Email", "Phone", "Website", "Category", "Address", "Rating", "Reviews", "Maps URL"]
EXCLUDED_DOMAINS = {"google.com", "facebook.com", "instagram.com"}
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}\b")
CONTACT_PATHS = ("contact", "about", "contact-us", "kontakt", "epikoinonia")


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
        api_key = os.environ.get("PLACES_API_KEY") or cfg.get("api_key", "").strip()
        if not api_key:
            log.info("No Google Places API key configured. Set PLACES_API_KEY env var or add api_key to config.")
            return

        queries = [
            f"{t.strip()} {loc.strip()}"
            for t in cfg["search_terms"].split(",") if t.strip()
            for loc in cfg["locations"].split(",") if loc.strip()
        ]
        if not queries:
            log.info("No search queries configured.")
            return

        limit = int(cfg.get("max_results", 20))
        async with aiohttp.ClientSession() as session:
            for q in queries:
                if not self.active:
                    break
                await self._search_places(session, api_key, q, limit)

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

    async def _search_places(self, session, api_key, q, limit):
        base_url = "https://places.googleapis.com/v1/places:searchText"
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "places.id,places.displayName,places.primaryType,places.formattedAddress,places.nationalPhoneNumber,places.websiteUri,places.rating,places.userRatingCount,places.googleMapsUri,nextPageToken",
        }

        page_token = None
        count = 0

        while self.active:
            if limit and count >= limit:
                break

            body = {"textQuery": q, "pageSize": min(20, limit - count) if limit else 20}
            if page_token:
                body["pageToken"] = page_token

            try:
                async with session.post(base_url, json=body, headers=headers) as resp:
                    if resp.status == 403:
                        log.warning("Invalid API key or Places API not enabled.")
                        return
                    if resp.status == 429:
                        log.warning("Rate limited. Try again later.")
                        return
                    if resp.status != 200:
                        log.warning(f"Places API error {resp.status}")
                        return
                    data = await resp.json()
            except Exception as e:
                log.warning(f"Places API request failed: {e}")
                return

            places = data.get("places", [])
            if not places:
                break

            page_info = "initial" if not page_token else "next"
            log.info(f"Searching: {q} ({page_info} page, {len(places)} results)")
            for place in places:
                if not self.active:
                    return
                if limit and count >= limit:
                    break

                maps_url = (place.get("googleMapsUri") or "").rstrip("/")
                async with self._lock:
                    if maps_url in self._seen_urls:
                        continue
                    self._seen_urls.add(maps_url)

                res = {
                    "Company": (place.get("displayName") or {}).get("text", ""),
                    "Category": place.get("primaryType", ""),
                    "Address": place.get("formattedAddress", ""),
                    "Phone": place.get("nationalPhoneNumber", ""),
                    "Website": (place.get("websiteUri") or "").rstrip("/"),
                    "Email": "",
                    "Rating": str(place.get("rating", "")),
                    "Reviews": str(place.get("userRatingCount", "")),
                    "Maps URL": maps_url,
                }

                count += 1
                async with self._lock:
                    self._append(res)
                log.info(f"Captured: {res['Company']}")

            page_token = data.get("nextPageToken")
            if not page_token:
                break

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
