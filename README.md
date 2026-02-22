# Maps Lead Scraper

Scrape business leads from Google Maps and extract contact details from their websites.

![Python](https://img.shields.io/badge/python-3.9+-blue.svg)
![Playwright](https://img.shields.io/badge/playwright-v1.58+-orange.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)

## Features

- Auto-scrolls Google Maps to collect maximum listings per query
- Detects direct-hit searches (single result pages) automatically
- Bypasses cookie consent screens
- Enriches every listing by visiting its website to extract emails and phones
- Real-time dashboard with live log panel, filtering, and CSV export
- Collapsible sidebar, resizable log panel, full mobile support

## Stack

- **FastAPI + uvicorn** — async web server, scraper runs on the same event loop
- **Playwright** — headless Chromium browser automation
- **Tailwind CSS + Alpine.js** — frontend, no build step

## Installation

```bash
git clone https://github.com/dacrab/maps-lead-scraper.git
cd maps-lead-scraper
pip install -r requirements.txt
playwright install chromium
```

## Usage

```bash
python3 main.py
```

Open `http://localhost:8000`, go to **Settings**, configure your search terms and locations, then hit **Start**.

## Configuration

| Setting | Description |
| :--- | :--- |
| **Search Terms** | Comma-separated business types, e.g. `Plumbers, Dentists` |
| **Locations** | Comma-separated cities or areas, e.g. `New York, London` |
| **Max Results** | Per-query limit. `0` = unlimited |
| **Headless** | Run browser in background (recommended) or visible for debugging |
| **Concurrency** | Number of parallel tabs for website enrichment |

## Project Structure

```
main.py           # Scraper + FastAPI server
templates/
└── index.html    # Dashboard + Settings SPA
static/           # Logo, favicon
contacts.csv      # Auto-saved leads
config.json       # Auto-saved settings
```

## License

MIT
