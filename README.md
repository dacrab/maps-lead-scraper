# Maps Lead Scraper

Find local businesses on Google Maps and pull contact emails from their websites.

**Live demo:** https://email-scraper-9svf.onrender.com

Type in a business category and a city, and the scraper searches Google Maps for matching companies. It then visits each company's website to look for a contact email, and collects everything into one table. Export the list as a CSV when you're done.

## Features

- Search any business type in any city or region
- Pages through Maps results to find more than the first few
- Checks each company's homepage, contact page, and about page for emails
- Live results table with search and CSV export
- Adjustable result limits and request concurrency
- Skips businesses it has already seen, so reruns stay tidy

## Run it locally

You'll need Python 3.12 or newer and a Google Places API key with the **Places API (New)** enabled.

```bash
git clone https://github.com/dacrab/maps-lead-scraper.git
cd maps-lead-scraper
pip install -r requirements.txt
export PLACES_API_KEY=your_key_here
python3 main.py
```

Open <http://localhost:8000>, click the gear icon to set your search terms and locations, then press **Start**.

## Good to know

- Searches use your own Google Places key, so they count against your Google Cloud usage.
- Email discovery only finds addresses a business has published on its site.
- Leads are saved to `data/contacts.csv` and settings to `config.json` on the machine running the app.
- Check your local rules on cold outreach before you use a list.

## Built with

Python, FastAPI, and aiohttp, with a small dashboard served by the same app.

## License

MIT
