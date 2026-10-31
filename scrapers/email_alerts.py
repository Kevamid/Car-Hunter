"""PistonHeads and Car & Classic listings from Kev's own saved-search alert EMAILS (compliant: the
sites email him; we read his mailbox and never open their pages or follow their links).

    python -m scrapers.email_alerts

Each run reads the last few days of alert emails (read-only IMAP), skips emails already handled
(Message-ID in `processed_emails`), parses listings, keeps those that match a switched-on search
(utils.searches; title rules and price limit as the other sources), alerts on new ones and saves them. An email
is marked handled only once all its alerts were sent, so a Telegram failure is retried next run.
An alert email that yields no listings triggers one warning: the site may have changed its format.
"""
import logging
import time

from scrapers.base import BaseScraper
from scrapers.email_parsers import parse_alert
from utils.db import get_db_connection, init_db, is_listing_seen, upsert_listing
from utils import searches
from utils.dedupe import duplicate_note, find_duplicate, year_from_title
from utils.market import price_check
from utils.supabase_sync import push_details
from utils.matching import title_matches
from utils.notifier import format_alert, send_telegram

SENDERS = {"pistonheads.com": "PistonHeads", "carandclassic.com": "Car & Classic", "autotrader.co.uk": "Autotrader"}
LOOKBACK_DAYS = 3
MAX_ALERTS_PER_RUN = 20


def _processed_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS processed_emails (
        message_id TEXT PRIMARY KEY, sender TEXT, subject TEXT, listings INTEGER,
        processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")


def is_processed(message_id):
    conn = get_db_connection()
    try:
        _processed_table(conn)
        return conn.execute("SELECT 1 FROM processed_emails WHERE message_id = ?", (message_id,)).fetchone() is not None
    finally:
        conn.close()


def mark_processed(message, listings):
    conn = get_db_connection()
    try:
        _processed_table(conn)
        conn.execute("INSERT OR IGNORE INTO processed_emails (message_id, sender, subject, listings) VALUES (?, ?, ?, ?)",
                     (message.message_id, message.sender, message.subject[:200], listings))
        conn.commit()
    finally:
        conn.close()


OTHER = {"make": "Other", "model": "cars"}  # pass-through listings: matched no search at all


def classify(listing, cars):
    """("match", car), ("skip", None) or ("other", None).

    A listing BELONGS to a search when its title has the search's base model word ("Clio", "MX-5"); it is a
    match only if that search is switched on and its full rules pass (must-have words, price). Listings that
    belong to no search are "other" (alerted only when pass-through is on), so a switched-off or over-budget
    MX-5, or a plain Clio 1.2, is never passed through. Prices in another currency (Car & Classic shows
    European cars in euros) mean a non-UK car: skipped, like the scrapers did."""
    if listing.get("currency") not in (None, "GBP"):
        return "skip", None
    belongs = False
    for car in cars:
        words = str(car.get("model") or "").split()
        if not title_matches(listing["title"], words, words[:1], False, []):
            continue
        belongs = True
        if car.get("enabled") is False:
            continue
        if not title_matches(listing["title"], words, words, False, car.get("aliases") or []):
            continue
        price, hi, lo = listing.get("price_value"), car.get("max_price"), car.get("min_price") or 0
        if price is not None and ((hi and price > hi) or price < lo):
            continue
        return "match", car
    return ("skip", None) if belongs else ("other", None)


def match_car(listing, cars):
    """The search this listing matches (switched on, title rules, price limit), or None."""
    kind, car = classify(listing, cars)
    return car if kind == "match" else None


class EmailAlerts(BaseScraper):
    def __init__(self, cars, notifier=None, fetch=None, alert_delay_s=1.1, ebay=None, pass_through=False):
        super().__init__("Email alerts")
        self.cars = cars
        self.pass_through = pass_through  # also alert on listings that belong to no search
        self.notifier = notifier or send_telegram
        self.fetch = fetch  # callable(sender_domains, days) -> [Message]; default: the real mailbox
        self.alert_delay_s = alert_delay_s
        self.ebay = ebay    # eBay client for price-against-market lines; None = no price lines

    def run(self):
        """Returns the number of new listings alerted."""
        if self.fetch is None:
            from utils.mailbox import fetch_recent
            self.fetch = fetch_recent
        new_total = sent = 0
        for message in self.fetch(list(SENDERS), LOOKBACK_DAYS):
            if not message.message_id or is_processed(message.message_id):
                continue
            site = next(name for domain, name in SENDERS.items() if message.sender.endswith(domain))
            listings = parse_alert(message.html, message.text, site)
            if not listings:
                self.notifier(f"⚠️ A {site} email (\"{message.subject[:80]}\") had no listings I could read. "
                              f"If it was a saved-search alert, its format may have changed.")
                mark_processed(message, 0)
                continue

            complete = True
            for listing in listings:
                kind, car = classify(listing, self.cars)
                if kind == "other" and self.pass_through:
                    car = OTHER
                if car is None or is_listing_seen(listing["url"]):
                    continue
                if sent >= MAX_ALERTS_PER_RUN:
                    complete = False  # leave the email for the next run
                    break
                link = listing.get("link") or listing["url"]  # Autotrader: its own tracked link, for Kev to tap
                year = year_from_title(listing["title"])
                duplicate = None if car is OTHER else find_duplicate(
                    car, {"price": listing.get("price_value"), "mileage": listing.get("mileage"),
                          "year": year, "title": listing["title"]}, site)
                market = None
                if duplicate:  # already alerted from another site: one line instead of a full alert
                    outcome = self.notifier(duplicate_note(duplicate, site, listing["title"], listing.get("price_value"), link))
                else:
                    if car is not OTHER:
                        market = price_check(self.ebay, car, listing.get("price_value"), year, listing["title"])
                    text = format_alert({
                        "title": listing["title"], "name": listing["title"], "source": site, "url": link,
                        "price": f"£{listing['price_value']:,}" if listing.get("price_value") else "price not in the email",
                        "mileage": listing.get("mileage"), "market": market,
                    })
                    if car is OTHER:
                        text += "\n🔎 Not one of your searches: from a saved search on the site"
                    outcome = self.notifier(text)
                if outcome is False:
                    complete = False  # Telegram failed: retry this email next run
                    continue
                sent += bool(outcome)
                if outcome:
                    time.sleep(self.alert_delay_s)
                if upsert_listing({
                    "url": listing["url"], "source": site, "external_id": listing["id"],
                    "make": car.get("make"), "model": car.get("model"), "variant": "", "title": listing["title"],
                    "price_raw": listing.get("price_value") or 0,
                    "price_formatted": f"£{listing['price_value']:,}" if listing.get("price_value") else "",
                    "mileage": listing.get("mileage"), "year": year,
                }):
                    new_total += 1
                    push_details(listing["url"], images=listing.get("images"), market=market,
                                 link=listing.get("link"), duplicate_of=(duplicate or {}).get("url"),
                                 basics={"source": site, "make": car.get("make"), "model": car.get("model"),
                                         "title": listing["title"], "price": listing.get("price_value"),
                                         "mileage": listing.get("mileage"), "year": year})
            if complete:
                mark_processed(message, len(listings))
            self.logger.info(f"{site} email \"{message.subject[:60]}\": {len(listings)} listings parsed")
        return new_total


def main():
    from dotenv import load_dotenv
    from utils import mailbox
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    if not mailbox.configured():
        logging.warning("Email alerts skipped: ALERTS_EMAIL / ALERTS_APP_PASSWORD not set.")
        return
    init_db()
    loaded = searches.load()
    pass_through = loaded["settings"].get("pass_through") is True
    logging.info(f"{len(loaded['searches'])} searches from {loaded['origin']}; pass-through {'on' if pass_through else 'off'}")
    from utils.ebay_api import EbayClient
    try:
        count = EmailAlerts(loaded["searches"], ebay=EbayClient() if EbayClient.configured() else None,
                            pass_through=pass_through).run()
    except mailbox.MailboxError as exc:
        logging.error(f"Email alerts failed: {exc}")
        return
    logging.info(f"Email alerts complete: {count} new listings.")


if __name__ == "__main__":
    main()
