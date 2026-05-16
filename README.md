# Maps Lead Scraper

Scrape business leads from Google Maps and extract emails from their websites.

![Python](https://img.shields.io/badge/python-3.10+-blue.svg)
![Playwright](https://img.shields.io/badge/playwright-v1.59+-orange.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)

## Features

- Auto-scrolls Google Maps to collect listings per query
- Detects direct-hit searches (single result pages)
- Bypasses cookie consent screens
- Enriches listings via fast HTTP requests — checks homepage + `/contact`, `/about` subpages for emails
- Real-time terminal-style dashboard with log panel, filtering, and CSV export

## Stack

- **FastAPI + uvicorn** — async web server
- **Playwright** — headless Chromium for Maps scraping
- **aiohttp** — fast async HTTP for email enrichment
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

Open `http://localhost:8000`, click the ⚙ gear icon to configure search terms and locations, then hit **Start**.

## Configuration

| Setting | Description |
| :--- | :--- |
| **Search Terms** | Comma-separated business types, e.g. `Plumbers, Dentists` |
| **Locations** | Comma-separated cities or areas, e.g. `New York, London` |
| **Max Results** | Per-query limit. `0` = unlimited |
| **Headless** | Run browser in background (recommended) |
| **Concurrency** | Parallel HTTP requests for email enrichment |

## Project Structure

```
main.py           # Scraper + FastAPI server
templates/
└── index.html    # Dashboard SPA
static/           # Logo, favicon
contacts.csv      # Auto-saved leads
config.json       # Auto-saved settings
```

## License

MIT
