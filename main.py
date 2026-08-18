import asyncio
import csv
import json
import logging
import os
import re
from collections import deque
from contextlib import asynccontextmanager
from http import HTTPStatus
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TypedDict

import aiohttp
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
STATE_DIR = BASE_DIR / "data"
STATE_DIR.mkdir(exist_ok=True)
DB_FILE = STATE_DIR / "contacts.csv"
CFG_FILE = BASE_DIR / "config.json"

PLACES_API_URL = "https://places.googleapis.com/v1/places:searchText"
PAGE_SIZE = 20

DEFAULT_CFG = {
    "search_terms": "", "locations": "",
    "api_key": "", "max_results": 20, "concurrency": 5,
}

FIELDS = [
    "Company", "Email", "Phone", "Website", "Category",
    "Address", "Rating", "Reviews", "Maps URL",
]
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}\b")
CONTACT_PATHS = ("contact", "about", "contact-us", "kontakt", "epikoinonia")

Lead = TypedDict(
    "Lead",
    {
        "Company": str, "Email": str, "Phone": str, "Website": str,
        "Category": str, "Address": str, "Rating": str, "Reviews": str,
        "Maps URL": str,
    },
    total=False,
)

Config = TypedDict(
    "Config",
    {
        "search_terms": str, "locations": str, "api_key": str,
        "max_results": int, "concurrency": int,
    },
    total=False,
)


def load_config() -> Config:
    if not CFG_FILE.exists():
        return dict(DEFAULT_CFG)
    try:
        cfg = json.loads(CFG_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_CFG)
    if not isinstance(cfg, dict):
        return dict(DEFAULT_CFG)
    return {**DEFAULT_CFG, **cfg}


def _sanitize_cfg(cfg: dict) -> Config | None:
    cleaned: dict = dict(DEFAULT_CFG)
    for key in DEFAULT_CFG:
        if key not in cfg:
            continue
        value = cfg[key]
        if key in ("max_results", "concurrency"):
            try:
                value = int(value)
            except (TypeError, ValueError):
                return None
            if key == "max_results":
                value = max(0, value)
            else:
                value = max(1, min(value, 10))
        elif not isinstance(value, str):
            return None
        cleaned[key] = value
    return cleaned


class MemoryHandler(logging.Handler):
    def __init__(self, capacity=50):
        super().__init__()
        self.buffer = deque(maxlen=capacity)

    def emit(self, record):
        self.buffer.append(self.format(record))


log_handler = MemoryHandler()
log = logging.getLogger("scraper")
log.setLevel(logging.INFO)
if not any(isinstance(h, MemoryHandler) for h in log.handlers):
    log.addHandler(log_handler)
if not any(isinstance(h, RotatingFileHandler) for h in log.handlers):
    log.addHandler(
        RotatingFileHandler(
            STATE_DIR / "scraper.log",
            maxBytes=1_000_000,
            backupCount=3,
        )
    )
logging.getLogger("uvicorn.access").setLevel(logging.ERROR)


def _places_headers(api_key):
    return {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": (
            "places.id,places.displayName,places.primaryType,places.formattedAddress,"
            "places.nationalPhoneNumber,places.websiteUri,places.rating,"
            "places.userRatingCount,places.googleMapsUri,nextPageToken"
        ),
    }


async def _fetch_page(session, api_key, body):
    try:
        async with session.post(
            PLACES_API_URL, json=body, headers=_places_headers(api_key)
        ) as resp:
            if resp.status == HTTPStatus.FORBIDDEN:
                log.warning("Invalid API key or Places API not enabled.")
                return None
            if resp.status == HTTPStatus.TOO_MANY_REQUESTS:
                log.warning("Rate limited. Try again later.")
                return None
            if resp.status != HTTPStatus.OK:
                log.warning(f"Places API error {resp.status}")
                return None
            return await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
        log.warning(f"Places API request failed: {e}")
        return None


class Engine:
    def __init__(self):
        self.active = False
        self._task: asyncio.Task | None = None
        self._seen_urls: set[str] = set()
        self._lock = asyncio.Lock()
        self._cache: list[Lead] = []
        self._cache_mtime: float = 0
        self._pending: list[Lead] = []
        if DB_FILE.exists():
            with open(DB_FILE, encoding="utf-8") as f:
                self._seen_urls = {r.get("Maps URL", "") for r in csv.DictReader(f)}

    def _read_leads(self) -> list[Lead]:
        if not DB_FILE.exists():
            return []
        with open(DB_FILE, encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def _read_leads_cached(self) -> list[Lead]:
        if not DB_FILE.exists():
            self._cache, self._cache_mtime = [], 0
            return []
        mtime = DB_FILE.stat().st_mtime
        if mtime != self._cache_mtime:
            self._cache = self._read_leads()
            self._cache_mtime = mtime
        return self._cache

    def _append(self, row: Lead, batch: int = 50):
        self._pending.append(row)
        if len(self._pending) >= batch:
            self._flush_pending()

    def _flush_pending(self):
        if not self._pending:
            return
        exists = DB_FILE.exists()
        with open(DB_FILE, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if not exists:
                w.writeheader()
            w.writerows(self._pending)
        self._pending.clear()

    def _rewrite(self, rows: list[Lead]):
        tmp = DB_FILE.with_suffix(".tmp")
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
        tmp.replace(DB_FILE)

    def _done(self, task):
        if self._task is task:
            self._task = None
        if task.cancelled():
            return
        if exc := task.exception():
            log.error(f"Scraper crashed: {exc}")

    async def stop(self):
        self.active = False
        if self._task and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def run(self, cfg):
        self.active = True
        try:
            await self._run(cfg)
        finally:
            self.active = False

    async def _run(self, cfg):
        log.info("Starting scraper...")
        api_key = os.environ.get("PLACES_API_KEY") or cfg["api_key"].strip()
        if not api_key:
            log.info(
                "No Google Places API key configured. "
                "Set PLACES_API_KEY env var or add api_key to config."
            )
            return

        queries = [
            f"{t.strip()} {loc.strip()}"
            for t in cfg["search_terms"].split(",") if t.strip()
            for loc in cfg["locations"].split(",") if loc.strip()
        ]
        if not queries:
            log.info("No search queries configured.")
            return

        limit = int(cfg["max_results"])
        async with aiohttp.ClientSession() as session:
            for q in queries:
                if not self.active:
                    break
                await self._search_places(session, api_key, q, limit)

        async with self._lock:
            self._flush_pending()
            if not self.active:
                return
            sites = [
                dict(r) for r in self._read_leads_cached()
                if r.get("Website") and not r.get("Email")
            ]
        if not sites:
            log.info("Done.")
            return

        log.info(f"Enriching {len(sites)} websites...")
        sem = asyncio.Semaphore(min(int(cfg["concurrency"]), 10))
        timeout = aiohttp.ClientTimeout(total=8)
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
        }
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            tasks = [
                asyncio.create_task(self._enrich(session, res, sem)) for res in sites
            ]
            while tasks:
                if not self.active:
                    for t in tasks:
                        t.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    break
                _, tasks = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED
                )

        async with self._lock:
            self._flush_pending()
            all_leads = self._read_leads_cached()
            emails = {
                r["Website"]: r.get("Email")
                for r in sites
                if r.get("Website") and r.get("Email")
            }
            for lead in all_leads:
                if email := emails.get(lead.get("Website")):
                    lead["Email"] = email
            self._rewrite(all_leads)
        log.info("Done.")

    async def _search_places(self, session, api_key, q, limit):
        page_token = None
        count = 0

        while self.active:
            if limit and count >= limit:
                break

            body = {
                "textQuery": q,
                "pageSize": min(PAGE_SIZE, limit - count) if limit else PAGE_SIZE,
            }
            if page_token:
                body["pageToken"] = page_token

            data = await _fetch_page(session, api_key, body)
            if data is None:
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
            urls = dict.fromkeys(
                [base] + [f"{base.rstrip('/')}/{p}" for p in CONTACT_PATHS]
            )
            for url in urls:
                try:
                    async with session.get(
                        url, allow_redirects=True
                    ) as resp:
                        if resp.status != HTTPStatus.OK:
                            continue
                        html = await resp.text(errors="ignore")
                    if m := EMAIL_RE.search(html):
                        res["Email"] = m.group(0).lower()
                        break
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    log.debug(f"Fetch failed for {url}: {e}")
                    continue
            log.info(
                f"Enriched: {res.get('Company', base)} "
                f"{'✓' if res.get('Email') else '—'}"
            )


engine = Engine()


@asynccontextmanager
async def lifespan(app):
    yield
    await engine.stop()


app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/api/status")
async def status():
    async with engine._lock:
        leads = engine._read_leads_cached()
    return {
        "running": engine.active,
        "leads": leads,
        "logs": list(log_handler.buffer),
        "config": load_config(),
    }


@app.post("/control/{action}")
async def control(action: str):
    if action == "start" and not engine.active and (
        engine._task is None or engine._task.done()
    ):
        engine._task = asyncio.create_task(engine.run(load_config()))
        engine._task.add_done_callback(engine._done)
    elif action == "stop":
        engine.active = False
    elif action == "clear":
        async with engine._lock:
            engine._seen_urls.clear()
            engine._cache = []
            engine._cache_mtime = 0
            engine._pending.clear()
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
    cleaned = _sanitize_cfg(cfg)
    if cleaned is None:
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
    uvicorn.run(
        app,
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
    )