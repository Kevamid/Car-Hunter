"""Fill in photos (and eBay descriptions) for dashboard cars that don't have any yet.

Photos are normally captured when a car is first alerted. This catches up cars alerted before
the dashboard existed, or whose alert-time capture failed, from legitimate sources only:
  * eBay: the official Browse API (photos, description, item specifics). Listings that have ended
    are marked `details.listing_status = "ended"` so they aren't retried every run.
  * PistonHeads / Car & Classic / Autotrader: the alert emails still in Kev's mailbox (never
    deleted), matched to each car by its listing URL (Autotrader: the title+price+mileage fingerprint).
Never opens the sites' own pages. Gentle: at most MAX_EBAY_LOOKUPS eBay calls per run.

    python -m utils.photo_backfill
"""
import json
import os
import urllib.request

from utils.supabase_sync import configured, update_car

MAX_EBAY_LOOKUPS = 60
EMAIL_DAYS = 30


def cars_without_photos():
    base, key = os.getenv("SUPABASE_URL", "").rstrip("/"), os.getenv("SUPABASE_SERVICE_KEY", "")
    request = urllib.request.Request(f"{base}/rest/v1/cars?select=url,source,details&images=eq.%5B%5D&limit=5000",
                                     headers={"apikey": key})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def backfill_ebay(cars, client, limit=MAX_EBAY_LOOKUPS):
    """Returns (filled, ended) counts."""
    from scrapers.ebay import enrich, listing_from_summary
    from utils.ebay_api import EbayApiError
    filled = ended = 0
    todo = [c for c in cars if c["source"] == "eBay" and (c.get("details") or {}).get("listing_status") != "ended"]
    for car in todo[:limit]:
        legacy = car["url"].rstrip("/").rsplit("/", 1)[-1]
        try:
            detail = client.get("/buy/browse/v1/item/get_item_by_legacy_id", legacy_item_id=legacy)
        except EbayApiError as exc:
            if exc.status == 404:  # listing ended or removed: remember, so it isn't retried
                update_car(car["url"], {"details": {**(car.get("details") or {}), "listing_status": "ended"}})
                ended += 1
            continue
        item = enrich(listing_from_summary({"legacyItemId": legacy, "itemId": detail.get("itemId", ""),
                                            "title": detail.get("title"), "image": detail.get("image")}), detail)
        if item.get("images"):
            fields = {"images": item["images"], "description": item.get("description") or None,
                      "details": {**(car.get("details") or {}), **(item.get("details") or {})}}
            update_car(car["url"], {k: v for k, v in fields.items() if v})
            filled += 1
    return filled, ended


def backfill_email(cars, fetch=None):
    """Photos for email-sourced cars from the alert emails still in the mailbox. Returns filled count."""
    from scrapers.email_alerts import SENDERS
    from scrapers.email_parsers import parse_alert
    wanted = {c["url"] for c in cars if c["source"] in SENDERS.values()}
    if not wanted:
        return 0
    if fetch is None:
        from utils.mailbox import fetch_recent
        fetch = fetch_recent
    photos = {}
    for message in fetch(list(SENDERS), EMAIL_DAYS):
        site = next(name for domain, name in SENDERS.items() if message.sender.endswith(domain))
        for listing in parse_alert(message.html, message.text, site):
            if listing["url"] in wanted and listing.get("images"):
                photos.setdefault(listing["url"], listing["images"])
    for url, images in photos.items():
        update_car(url, {"images": images})
    return len(photos)


def main():
    from dotenv import load_dotenv
    load_dotenv()
    if not configured():
        print("Photo backfill skipped: SUPABASE_URL / SUPABASE_SERVICE_KEY not set.")
        return
    try:
        cars = cars_without_photos()
    except Exception as exc:
        print(f"Photo backfill skipped: could not read the dashboard ({type(exc).__name__})")
        return
    report = [f"{len(cars)} cars without photos"]
    from utils.ebay_api import EbayClient
    if EbayClient.configured():
        filled, ended = backfill_ebay(cars, EbayClient())
        report.append(f"eBay: {filled} filled, {ended} ended")
    from utils import mailbox
    if mailbox.configured():
        try:
            report.append(f"email: {backfill_email(cars)} filled")
        except mailbox.MailboxError as exc:
            report.append(f"email: skipped ({exc})")
    print("Photo backfill: " + "; ".join(report))


if __name__ == "__main__":
    main()
