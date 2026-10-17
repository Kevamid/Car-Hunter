"""Car & Classic scraper.

Car & Classic is a Laravel + Inertia site. Every search page embeds its results as
JSON in <script data-page="app" type="application/json">, under
props.searchResults.data (about 60 per page) with props.searchResults.pagination.
Reading that JSON means no CSS selectors and no guessing at card text.

Search URL format (confirmed from the site, Oct 2026):
    /search?make=renault&model=clio&sort=latest&page=2
- make / model are slugs. The page echoes what it applied in props.searchParams.
  A real model echoes a proper label ("gt86" -> "GT86"); an unknown slug echoes
  itself ("clio-sport" -> "clio-sport") and usually matches nothing.
- sort=latest is "Newest listed". Listings carry no listed date, so freshness
  comes from scanning the newest pages and the database's de-duplication.
- price_to, query and country are IGNORED by the site, so price, country and
  keyword filtering all happen here.
- Prices are in minor units (pence/cents) and in mixed currencies (GBP, EUR, USD).
- Listing types: basic / offerable (classifieds), mao (make an offer), auction.
"""
import json
import logging
import re
import time
from urllib.parse import urlencode
from playwright.sync_api import sync_playwright
from scrapers.base import BaseScraper
from utils.matching import any_alias_in_text
from utils.db import is_listing_seen, is_search_seeded, mark_search_seeded, upsert_listing
from utils.notifier import format_alert, send_telegram

BASE_URL = "https://www.carandclassic.com"
SOURCE = "Car & Classic"
KM_TO_MILES = 0.621371
DATA_PAGE_RE = re.compile(r'<script[^>]*data-page="app"[^>]*>(.*?)</script>', re.S)


def _norm(text):
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


def read_page_props(html):
    """Return the Inertia page props, or None if the page has no embedded JSON."""
    match = DATA_PAGE_RE.search(html)
    if not match:
        return None
    return json.loads(match.group(1)).get("props")


def listing_from_result(result):
    """Flatten one entry of props.searchResults.data."""
    price = result.get("price") or {}
    minor = price.get("value")
    currency = (price.get("currency") or {}).get("name")
    pounds = minor // 100 if minor else None
    attrs = result.get("attributes") or {}
    mileage = (attrs.get("mileage") or {})
    miles = mileage.get("value")
    if miles is not None and mileage.get("unit") == "km":
        miles = round(miles * KM_TO_MILES)
    where = result.get("location") or {}
    year = str(result.get("year") or "")
    return {
        "id": str(result.get("slug") or result.get("id")),
        "type": result.get("type"),
        "url": BASE_URL + result.get("url", ""),
        "title": (result.get("title") or "").strip(),
        "price_value": pounds,
        "currency": currency,
        "price": f"£{pounds:,}" if pounds and currency == "GBP" else None,
        "mileage": miles,
        "year": int(year) if year.isdigit() else None,
        "transmission": (attrs.get("transmissionType") or "").capitalize() or None,
        "fuel": (attrs.get("fuelType") or "").capitalize() or None,
        "location": ", ".join(x for x in (where.get("town"), where.get("region")) if x) or None,
        "country": where.get("countryCode"),
        "seller_type": "Dealer" if result.get("seller") else None,
        "is_sold": bool(result.get("isSold")),
        "source": SOURCE,
    }


class CarAndClassicScraper(BaseScraper):
    def __init__(self, config=None, notifier=None):
        super().__init__(SOURCE)
        self.config = config or {}
        self.notifier = notifier or send_telegram  # callable(text) -> True / False / None
        self.max_alerts = self.config.get("max_alerts_per_query", 15)
        self.alert_delay_s = self.config.get("alert_delay_s", 1.1)  # Telegram: ~1 msg/s per chat
        self.max_pages = self.config.get("max_pages", 2)            # newest ~120 per search
        self.page_delay_s = self.config.get("page_delay_s", 2)      # be gentle between page loads
        self.include_auctions = self.config.get("include_auctions", False)  # price = current bid

    def build_url(self, make_slug, model_slug=None, page=1):
        params = {"make": make_slug}
        if model_slug:
            params["model"] = model_slug
        params["sort"] = "latest"
        if page > 1:
            params["page"] = page
        return f"{BASE_URL}/search?{urlencode(params)}"

    def _load(self, page, url):
        if self._loaded:
            time.sleep(self.page_delay_s)
        self._loaded += 1
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        props = read_page_props(page.content())
        if props is None or "searchResults" not in props:
            raise RuntimeError(f"no search results JSON on {url} (blocked or page changed?)")
        return props

    def _scan(self, page, make_slug, model_slug=None, first=None):
        """Results from the newest `max_pages` pages of one search."""
        results, props, number = [], first, 1
        while True:
            props = props or self._load(page, self.build_url(make_slug, model_slug, number))
            results += props["searchResults"]["data"]
            last = props["searchResults"]["pagination"].get("last_page") or 1
            if number >= min(last, self.max_pages):
                return results
            number, props = number + 1, None

    def _search(self, page, make, model):
        """Run the searches for one car. Returns (raw results, model words the site
        did NOT filter on, whether the site applied any model filter)."""
        make_slug = slugify(make)
        words = model.split()
        results = []
        # Longest model name the site recognises: "Clio Sport" -> "clio-sport" (unknown) -> "clio".
        for k in range(len(words), 0, -1):
            slug = slugify(" ".join(words[:k]))
            props = self._load(page, self.build_url(make_slug, slug))
            echoed = props.get("searchParams") or {}
            if (echoed.get("make") or {}).get("slug") != make_slug:
                raise RuntimeError(f"Car & Classic does not recognise make {make!r} (slug {make_slug!r})")
            applied = echoed.get("model") or {}
            if applied.get("slug") == slug and applied.get("label") != slug:
                return results + self._scan(page, make_slug, slug, first=props), words[k:], True
            results += props["searchResults"]["data"]  # unknown slug: keep any exact hits (e.g. "m140i")
        # No model the site knows: newest listings for the make, matched on title.
        return results + self._scan(page, make_slug), words, False

    def fetch_listings(self, car_config: dict) -> list:
        """Return matching listings (list of dicts). No database, no alerts.
        Raises if the site cannot be read, so a failed run is not mistaken for 'nothing new'."""
        make = str(car_config.get("make") or "").strip()
        model = str(car_config.get("model") or "").strip()
        max_price = car_config.get("max_price")
        min_price = car_config.get("min_price")
        aliases = car_config.get("aliases") or []

        self._loaded = 0
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
            )
            try:
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                    viewport={"width": 1280, "height": 800}
                )
                raw, unmatched, model_applied = self._search(context.new_page(), make, model)
            finally:
                browser.close()

        skipped = {"duplicate": 0, "auction": 0, "sold": 0, "not_uk": 0, "no_price": 0,
                   "price": 0, "title": 0, "written_off": 0}
        kept, seen = [], set()
        for result in raw:
            item = listing_from_result(result)
            title = item["title"]
            if item["id"] in seen:
                reason = "duplicate"
            elif item["type"] == "auction" and not self.include_auctions:
                reason = "auction"
            elif item["is_sold"]:
                reason = "sold"
            elif item["currency"] != "GBP" or item["country"] not in ("GB", None):
                reason = "not_uk"
            elif not item["price_value"]:
                reason = "no_price"  # POA / no asking price
            elif (max_price and item["price_value"] > max_price) or (min_price and item["price_value"] < min_price):
                reason = "price"
            elif not self._title_matches(title, words=model.split(), unmatched=unmatched,
                                         model_applied=model_applied, aliases=aliases):
                reason = "title"
            elif self.is_written_off(title, ""):
                reason = "written_off"
            else:
                reason = None
            seen.add(item["id"])
            if reason:
                skipped[reason] += 1
            else:
                kept.append(item)

        self.logger.info(f"{make} {model}: pages={self._loaded} scanned={len(raw)} kept={len(kept)} "
                         f"model_filter={'site' if model_applied else 'title'} skipped={skipped}")
        return kept

    @staticmethod
    def _title_matches(title, words, unmatched, model_applied, aliases):
        """Title check for whatever the site did not filter.

        - aliases given: the title needs one alias as a whole word ("Clio 200", "Clio RS"),
          plus the base model word if the site applied no model filter at all
          (so "Megane RS" does not pass for "Clio Sport").
        - no aliases: every model word the site did not apply must be in the title
          ("M140i" -> "2017 BMW M140I").
        """
        if aliases:
            base_ok = model_applied or not words or _norm(words[0]) in _norm(title)
            return base_ok and any_alias_in_text(title, aliases)
        return all(_norm(w) in _norm(title) for w in unmatched)

    @staticmethod
    def _to_db_row(item, car_config):
        return {
            "url": item["url"],
            "source": SOURCE,
            "external_id": item["id"],
            "make": car_config.get("make"),
            "model": car_config.get("model"),
            "variant": "",
            "title": item["title"],
            "price_raw": item["price_value"],
            "price_formatted": item["price"],
            "mileage": item["mileage"],
            "year": item["year"],
            "transmission": item["transmission"],
            "fuel_type": item["fuel"],
            "location": item["location"],
            "seller_type": item["seller_type"],
        }

    def scrape(self, car_config: dict) -> int:
        """Fetch, skip what the database already has, alert on the rest.

        Returns the number of NEW listings. A listing is only saved once its alert
        has been sent, so a failed Telegram call is retried on the next run.
        The first run of a search saves everything without alerting (see is_search_seeded).
        """
        make, model = car_config.get("make"), car_config.get("model")
        search_key = f"{SOURCE}|{make}|{model}".lower()
        seeding = not is_search_seeded(search_key)
        new_count = sent = unsent = 0

        for item in self.fetch_listings(car_config):
            row = self._to_db_row(item, car_config)
            if is_listing_seen(row["url"]):
                upsert_listing(row)  # refreshes last_seen and records price changes
                continue

            if seeding:
                pass  # first run: save quietly
            elif sent < self.max_alerts:
                outcome = self.notifier(format_alert({**item, "name": item["title"]}))
                if outcome is False:
                    continue  # send failed: leave unsaved so the next run retries it
                if outcome:
                    sent += 1
                    time.sleep(self.alert_delay_s)
            else:
                unsent += 1  # over the cap: still saved, summarised below

            if upsert_listing(row):
                new_count += 1
                self.logger.info(f"{'Seeded' if seeding else '✨ NEW'}: {item['title']} ({item['price']}) -> {item['url']}")

        if seeding:
            mark_search_seeded(search_key)
            self.logger.info(f"First run for {make} {model}: saved {new_count} current listings without alerts.")
            return 0
        if unsent:
            self.notifier(f"➕ {unsent} more new {make} {model} listings on {SOURCE} were saved "
                          f"but not sent (limit {self.max_alerts} alerts per search).")
        return new_count
