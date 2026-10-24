"""PistonHeads and Car & Classic listings from Kev's own saved-search alert EMAILS (compliant: the
sites email him; we read his mailbox and never open their pages or follow their links).

    python -m scrapers.email_alerts

Each run reads the last few days of alert emails (read-only IMAP), skips emails already handled
(Message-ID in `processed_emails`), parses listings, keeps those that match a car in config.json
(title rules and price limit as the other sources), alerts on new ones and saves them. An email
is marked handled only once all its alerts were sent, so a Telegram failure is retried next run.
An alert email that yields no listings triggers one warning: the site may have changed its format.
"""
import json
import logging
import time

from scrapers.base import BaseScraper
from scrapers.email_parsers import parse_alert
from utils.db import get_db_connection, init_db, is_listing_seen, upsert_listing
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


def match_car(listing, cars):
    """The config car this listing belongs to, or None (title rules + price limit). Prices in another
    currency (Car & Classic shows European cars in euros) mean a non-UK car: skipped, like the scrapers did."""
    if listing.get("currency") not in (None, "GBP"):
        return None
    for car in cars:
        model = str(car.get("model") or "")
        if not title_matches(listing["title"], model.split(), model.split(), False, car.get("aliases") or []):
            continue
        price, hi, lo = listing.get("price_value"), car.get("max_price"), car.get("min_price") or 0
        if price is not None and ((hi and price > hi) or price < lo):
            continue
        return car
    return None


class EmailAlerts(BaseScraper):
    def __init__(self, cars, notifier=None, fetch=None, alert_delay_s=1.1):
        super().__init__("Email alerts")
        self.cars = cars
        self.notifier = notifier or send_telegram
        self.fetch = fetch  # callable(sender_domains, days) -> [Message]; default: the real mailbox
        self.alert_delay_s = alert_delay_s

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
                car = match_car(listing, self.cars)
                if car is None or is_listing_seen(listing["url"]):
                    continue
                if sent >= MAX_ALERTS_PER_RUN:
                    complete = False  # leave the email for the next run
                    break
                outcome = self.notifier(format_alert({
                    "title": listing["title"], "name": listing["title"], "source": site,
                    "url": listing.get("link") or listing["url"],  # Autotrader: its own tracked link, for Kev to tap
                    "price": f"£{listing['price_value']:,}" if listing.get("price_value") else "price not in the email",
                    "mileage": listing.get("mileage"),
                }))
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
                    "mileage": listing.get("mileage"),
                }):
                    new_total += 1
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
    with open("config.json") as f:
        cars = json.load(f)
    try:
        count = EmailAlerts(cars).run()
    except mailbox.MailboxError as exc:
        logging.error(f"Email alerts failed: {exc}")
        return
    logging.info(f"Email alerts complete: {count} new listings.")


if __name__ == "__main__":
    main()
