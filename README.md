# Maps Lead Scraper

Point it at a business category and a city, and it brings back companies plus the emails published on their websites.

**Live demo:** https://email-scraper-9svf.onrender.com

The dashboard runs the search, shows leads as they arrive, and lets you filter the list before exporting a CSV.

## What it does

- Searches Google Maps through the Places API, no browser automation
- Works through several pages of results per query
- Visits each company's homepage, contact page, and about page looking for an address
- Keeps a live log and a searchable table
- Remembers companies it has already found, so a rerun doesn't repeat them
- Lets you tune the result cap and how many requests run at once

## Running the scraper

Python 3.12 or newer, plus a Google Places API key with the **Places API (New)** enabled.

```bash
git clone https://github.com/dacrab/maps-lead-scraper.git
cd maps-lead-scraper
pip install -r requirements.txt
export PLACES_API_KEY=your_key_here
python3 main.py
```

Visit <http://localhost:8000>, open the gear menu, enter your search terms and locations, and hit **Start**.

## Keep in mind

- The key is yours, so searches draw on your Google Cloud quota.
- Many businesses don't publish an email; the scraper can only find what's public.
- Results land in `data/contacts.csv`, settings in `config.json`.
- Cold-outreach rules vary by country — check yours first.

MIT licensed — see [LICENSE](LICENSE).
