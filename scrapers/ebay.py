"""eBay Motors UK via the official Browse API: the compliant listing source.

One search per car: category 9801 (Cars), "make model" keywords, newest first, UK only,
price window and buying format filtered by eBay. Auctions are excluded, including
"Buy It Now + auction" listings (Kev's rule: no auctions). Freshness uses the listing's
original date (`itemOriginDate`), so a relisted old car does not alert again as new.

Only listings that are new to the database are opened in full (one extra call each) to
read eBay's item specifics: mileage, year, owners, MOT expiry, service history.
"""
import re
import time
from datetime import datetime, timedelta, timezone

from scrapers.base import BaseScraper
from utils.db import is_listing_seen, is_search_seeded, mark_search_seeded, upsert_listing
from utils.ebay_api import EbayClient
from utils.matching import title_matches
from utils.notifier import format_alert, send_telegram, send_telegram_photo

SOURCE = "eBay"
FRESH_HOURS = 48
BUYING_FILTER = "buyingOptions:{FIXED_PRICE|CLASSIFIED_AD|BEST_OFFER}"


def _int(text):
    digits = re.sub(r"[^\d]", "", str(text or ""))
    return int(digits) if digits else None


def _when(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def listing_from_summary(summary):
    """Flatten one search result (itemSummaries entry)."""
    legacy = str(summary.get("legacyItemId") or (summary.get("itemId", "").split("|") + ["", ""])[1])
    price = summary.get("price") or {}
    location = summary.get("itemLocation") or {}
    return {
        "id": legacy,
        "item_id": summary.get("itemId", ""),
        "title": (summary.get("title") or "").strip(),
        "price_value": round(float(price.get("value", 0))) if price.get("value") else None,
        "currency": price.get("currency"),
        "price": None,
        "buying": summary.get("buyingOptions") or [],
        "listed": _when(summary.get("itemOriginDate") or summary.get("itemCreationDate")),
        "url": f"https://www.ebay.co.uk/itm/{legacy}",
        "image": (summary.get("image") or {}).get("imageUrl"),
        "location": location.get("city") or (location.get("postalCode") or "").rstrip("*") or None,
        "country": location.get("country"),
        "mileage": None, "year": None, "fuel": None, "transmission": None, "seller_type": None,
        "source": SOURCE,
    }


def enrich(listing, detail):
    """Add eBay's item specifics from a full listing (Browse getItem)."""
    aspects = {a.get("name"): a.get("value") for a in detail.get("localizedAspects") or []}
    seller = (detail.get("seller") or {}).get("sellerAccountType")
    location = detail.get("itemLocation") or {}
    extra = [f"{aspects['Number of Previous Owners']} previous owners" if aspects.get("Number of Previous Owners") else None,
             f"MOT to {aspects['MOT Expiry']}" if aspects.get("MOT Expiry") else None,
             f"service history: {aspects['Service History Available'].lower()}" if aspects.get("Service History Available") else None]
    listing.update({
        "mileage": _int(aspects.get("Mileage")),
        "year": _int(aspects.get("Year")),
        "fuel": aspects.get("Fuel Type") or aspects.get("Fuel"),
        "transmission": aspects.get("Transmission"),
        "seller_type": {"BUSINESS": "Dealer", "INDIVIDUAL": "Private"}.get(seller),
        "location": location.get("city") or listing["location"],
        "name": listing["title"],
        "extra": " · ".join(x for x in extra if x) or None,
    })
    return listing


class EbayScraper(BaseScraper):
    def __init__(self, config=None, notifier=None, photo_notifier=None, client=None):
        super().__init__(SOURCE)
        self.config = config or {}
        self.client = client or EbayClient()
        self.notifier = notifier or send_telegram               # callable(text) -> True / False / None
        self.photo_notifier = photo_notifier or send_telegram_photo  # callable(url, caption)
        self.max_alerts = self.config.get("max_alerts_per_query", 15)
        self.alert_delay_s = self.config.get("alert_delay_s", 1.1)
        self.fresh_hours = self.config.get("fresh_hours", FRESH_HOURS)

    @staticmethod
    def _query(car):
        """Keywords: make + model; with aliases, make + the base model word ("Renault Clio")."""
        make, model = str(car.get("make") or "").strip(), str(car.get("model") or "").strip()
        words = model.split()
        return " ".join([make] + (words[:1] if car.get("aliases") and words else words)).strip()

    def fetch_listings(self, car, now=None):
        """Matching fresh listings (list of dicts, not yet enriched). No database, no alerts."""
        now = now or datetime.now(timezone.utc)
        lo, hi = car.get("min_price") or 0, car.get("max_price")
        price = f",price:[{lo}..{hi}],priceCurrency:GBP" if hi else ""
        results = self.client.search(self._query(car), filter=f"{BUYING_FILTER}{price},itemLocationCountry:GB")

        model, aliases = str(car.get("model") or ""), car.get("aliases") or []
        skipped = {"auction": 0, "old": 0, "price": 0, "title": 0, "written_off": 0}
        kept = []
        for summary in results:
            item = listing_from_summary(summary)
            if "AUCTION" in item["buying"]:
                reason = "auction"
            elif item["listed"] and now - item["listed"] > timedelta(hours=self.fresh_hours):
                reason = "old"
            elif not item["price_value"] or (hi and item["price_value"] > hi) or item["price_value"] < lo:
                reason = "price"
            elif not title_matches(item["title"], model.split(), model.split(), False, aliases):
                reason = "title"
            elif self.is_written_off(item["title"], ""):
                reason = "written_off"
            else:
                reason = None
            if reason:
                skipped[reason] += 1
            else:
                item["price"] = f"£{item['price_value']:,}"
                kept.append(item)
        self.logger.info(f"{car.get('make')} {model}: results={len(results)} kept={len(kept)} skipped={skipped}")
        return kept

    @staticmethod
    def _to_db_row(item, car):
        return {
            "url": item["url"], "source": SOURCE, "external_id": item["id"],
            "make": car.get("make"), "model": car.get("model"), "variant": "",
            "title": item["title"], "price_raw": item["price_value"], "price_formatted": item["price"],
            "mileage": item["mileage"], "year": item["year"], "transmission": item["transmission"],
            "fuel_type": item["fuel"], "location": item["location"], "seller_type": item["seller_type"],
        }

    def _alert(self, item):
        text = format_alert(item) + (f"\n{item['extra']}" if item.get("extra") else "")
        if item.get("image"):
            outcome = self.photo_notifier(item["image"], text)
            if outcome is not False:
                return outcome
        return self.notifier(text)  # no image, or the photo failed: plain message instead

    def scrape(self, car):
        """Fetch, skip what the database already has, alert on the rest (photo + details).

        Returns the number of NEW listings. A listing is saved only once its alert has been
        sent, so a failed Telegram call is retried next run. A search's first run saves
        without alerting (seeded_searches), like Car & Classic.
        """
        make, model = car.get("make"), car.get("model")
        search_key = f"{SOURCE}|{make}|{model}".lower()
        seeding = not is_search_seeded(search_key)
        new_count = sent = unsent = 0

        for item in self.fetch_listings(car):
            if is_listing_seen(item["url"]):
                upsert_listing(self._to_db_row(item, car))  # refresh last_seen, record price changes
                continue
            if not seeding:
                try:
                    enrich(item, self.client.item(item["item_id"]))
                except Exception as exc:  # details are a bonus; alert on the summary alone
                    self.logger.warning(f"Could not load details for {item['url']}: {type(exc).__name__}")
                if sent < self.max_alerts:
                    outcome = self._alert(item)
                    if outcome is False:
                        continue  # send failed: leave unsaved so the next run retries it
                    if outcome:
                        sent += 1
                        time.sleep(self.alert_delay_s)
                else:
                    unsent += 1
            if upsert_listing(self._to_db_row(item, car)):
                new_count += 1

        if seeding:
            mark_search_seeded(search_key)
            self.logger.info(f"First eBay run for {make} {model}: saved {new_count} listings without alerts.")
            return 0
        if unsent:
            self.notifier(f"➕ {unsent} more new {make} {model} listings on {SOURCE} were saved "
                          f"but not sent (limit {self.max_alerts} alerts per search).")
        return new_count
