"""Shared constants and configuration for email scraper."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent
CSV_FILE = BASE_DIR / "recipients.csv"
CONFIG_FILE = BASE_DIR / "config.json"
TEMPLATE_DIR = BASE_DIR / "templates"

# Patterns
EMAIL_REGEX = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"

PHONE_PATTERNS = [
    r"\+?\d{1,3}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,9}",
    r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}",
]

# Filters (all lowercase for case-insensitive matching)
INVALID_EMAIL_PATTERNS = [
    "example.com", "@example", ".png", ".jpg", ".gif",
    "sampleemail", "youremail", "noreply", "wixpress", "sentry", "qodeinteractive",
]

SKIP_DOMAINS = [
    "google", "facebook", "instagram", "youtube", "linkedin",
    "twitter", "gstatic", "googleapis", "schema.org",
]

# Contact page keywords (lowercase for case-insensitive matching)
CONTACT_KEYWORDS = ["contact", "kontakt", "contacto", "contatto", "contactez", "impressum", "about"]

# Google Maps selectors
MAPS_RESULT_SELECTORS = [
    "a[href*='/maps/place/']",
    "div.Nv2PK a",
    "a.hfpxzc",
    "div[role='article'] a",
]


@dataclass
class Config:
    """Scraper configuration."""
    output_filename: str = "recipients.csv"
    search_term: str = ""
    locations: list[str] = field(default_factory=list)
    max_results_per_query: int = 0
    phone_min_digits: int = 10
    headless: bool = True
    max_concurrent_pages: int = 5
    scroll_pause_time: float = 2.0
    max_scroll_attempts: int = 20
    delay_between_queries: tuple[float, float] = (3.0, 5.0)

    @classmethod
    def load(cls, path: str | Path | None = None) -> Config:
        """Load configuration from JSON file."""
        config_path = Path(path) if path else CONFIG_FILE
        if not config_path.exists():
            return cls()

        with config_path.open() as f:
            data = json.load(f)

        return cls(
            output_filename=data.get("output_filename", "recipients.csv"),
            search_term=data.get("search_term", ""),
            locations=data.get("locations", []),
            max_results_per_query=data.get("max_results_per_query", 0),
            phone_min_digits=data.get("phone_min_digits", 10),
            headless=data.get("headless", True),
            max_concurrent_pages=data.get("max_concurrent_pages", 5),
            scroll_pause_time=data.get("scroll_pause_time", 2.0),
            max_scroll_attempts=data.get("max_scroll_attempts", 20),
            delay_between_queries=(
                data.get("delay_between_queries_seconds_min", 3.0),
                data.get("delay_between_queries_seconds_max", 5.0),
            ),
        )

    def to_dict(self) -> dict:
        """Convert to dictionary (for JSON serialization)."""
        d = asdict(self)
        d["delay_between_queries_seconds_min"] = self.delay_between_queries[0]
        d["delay_between_queries_seconds_max"] = self.delay_between_queries[1]
        del d["delay_between_queries"]
        return d

    def save(self, path: str | Path | None = None) -> None:
        """Save configuration to JSON file."""
        config_path = Path(path) if path else CONFIG_FILE
        with config_path.open("w") as f:
            json.dump(self.to_dict(), f, indent=4)
