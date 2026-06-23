# Maps Lead Scraper

Scrape business leads from Google Maps (via the Places API) and extract emails from their websites.

![Python](https://img.shields.io/badge/python-3.12+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)

## Features

- Searches Google Maps via the **Places API (New)** — no browser needed, pure HTTP
- Paginates through results to discover more businesses beyond the top rankings
- Enriches listings via fast HTTP requests — checks homepage + `/contact`, `/about` subpages for emails
- Real-time terminal-style dashboard with log panel, filtering, and CSV export

## Stack

- **FastAPI + uvicorn** — async web server
- **Google Places API (New)** — Text Search for business discovery
- **aiohttp** — fast async HTTP for API calls and email enrichment
- **Tailwind CSS + Alpine.js** — frontend, no build step

## Installation

```bash
git clone https://github.com/dacrab/maps-lead-scraper.git
cd maps-lead-scraper
pip install -r requirements.txt
```

## Usage

Requires a [Google Places API key](https://console.cloud.google.com/apis/credentials) with the **Places API (New)** enabled.

```bash
export PLACES_API_KEY=your_key_here
python3 main.py
```

Or set the key via a `.env` file or Render secret. Open `http://localhost:8000`, click the ⚙ gear icon to configure search terms and locations, then hit **Start**.

## Configuration

| Setting | Description |
| :--- | :--- |
| **Search Terms** | Comma-separated business types, e.g. `Plumbers, Dentists` |
| **Locations** | Comma-separated cities or areas, e.g. `New York, London` |
| **Max Results** | Per-query limit. `0` = unlimited |
| **Concurrency** | Parallel HTTP requests for email enrichment |
| **PLACES_API_KEY** | Set via environment variable or Render secret |

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
