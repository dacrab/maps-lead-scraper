# Maps Lead Scraper ⚡

A straightforward tool to scrape business leads from Google Maps and extract contact details from their websites.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.9+-blue.svg)
![Playwright](https://img.shields.io/badge/playwright-v1.40+-orange.svg)

## 🚀 Features

*   **Single-File Engine**: The entire backend (Scraper + Web Server + API) lives in one file (`main.py`).
*   **Real-Time Dashboard**: A modern, responsive Single-Page Application (SPA) built with Tailwind & Alpine.js.
*   **In-Memory Speed**: No database required. The UI updates instantly as the scraper finds leads.
*   **Smart Scraping**:
    *   Auto-scrolls Google Maps to find maximum results.
    *   Bypasses "Accept Cookies" consent screens automatically.
    *   Detects "Direct Hit" searches (when Maps skips the list and goes to a single result).
*   **Data Enrichment**: Visits every business website found to extract emails and phone numbers using regex.
*   **CSV Export**: One-click export to a clean CSV file.

## 🛠️ Installation

1.  **Clone the repository**
    ```bash
    git clone https://github.com/dacrab/maps-lead-scraper.git
    cd maps-lead-scraper
    ```

2.  **Install dependencies**
    ```bash
    pip install -r requirements.txt
    playwright install chromium
    ```

## ⚡ Usage

1.  **Start the app**
    ```bash
    python3 main.py
    ```

2.  **Open your browser**
    Go to `http://localhost:8000`

3.  **Configure & Run**
    *   Go to the **Settings** tab.
    *   Enter your **Search Terms** (e.g., `Real Estate, Plumbers`) and **Locations** (e.g., `New York, London`).
    *   Set **Max Results** (use `0` for unlimited).
    *   Click **Start** on the Dashboard.

## ⚙️ Configuration

| Setting | Description |
| :--- | :--- |
| **Search Terms** | Comma-separated list of business categories to find. |
| **Locations** | Comma-separated list of cities/areas to search in. |
| **Max Results** | Limit per search query. Set to `0` to scrape everything found. |
| **Headless** | **ON** (Recommended): Runs in background. **OFF**: Shows the browser window (good for debugging). |
| **Concurrency** | (Internal) Defaults to 5-10 concurrent tabs for website crawling. |

## 📂 Project Structure

```text
.
├── main.py           # The Heart. Config + Scraper + Flask Server.
├── templates/
│   └── index.html    # The Face. Dashboard + Settings UI.
├── static/           # Assets (Logo, Favicon).
├── contacts.csv      # The Loot. Auto-saved leads.
└── config.json       # Auto-saved user settings.
```

## 📝 License

MIT License - feel free to modify and use for your own business.
