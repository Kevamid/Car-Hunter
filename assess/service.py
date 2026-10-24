"""One entry point for "assess this": used by the command line and the Telegram bot."""
import re
from urllib.parse import urlparse

from assess.checks import run_checks
from assess.ebay_link import assess_ebay, find_item_id
from assess.repairs import add_repairs
from utils.market import add_market
from assess.mot import MotApiError, MotClient, normalise_registration
from assess.parse import parse_advert
from assess.report import build_report
from assess.vehicle import vehicle_from_api

HELP = ("Send me a registration, optionally with what the advert claims, e.g.\n"
        "AB12CDE £8,995 74k miles 2009 silver\n"
        "or paste the whole advert text (it must include the registration), or send an eBay listing link.\n\n"
        "I check it against the official DVSA MOT records: mileage, MOT history, recalls and "
        "whether the advert matches the record.")


URL_RE = re.compile(r"https?://\S+|(?:www\.)?[a-z0-9-]+\.(?:co\.uk|com)/\S*", re.I)
SITES = {
    "pistonheads.": "PistonHeads", "autotrader.": "Autotrader", "carandclassic.": "Car & Classic",
    "gumtree.": "Gumtree", "facebook.": "Facebook Marketplace", "cargurus.": "CarGurus", "motors.": "Motors",
}
SHARE_TIP = ("Copy the advert's text (select all on the page) and paste it here, as long as it shows the "
             "registration. Or just send the registration with the price and mileage, e.g.\n"
             "AB12CDE £8,995 74k miles")


def link_reply(text):
    """Reply for a message that is only a link (no registration). The bot never opens
    advert pages itself: that would be automated access to the site."""
    url = URL_RE.search(text)
    if not url:
        return None
    host = urlparse(url.group(0) if "://" in url.group(0) else "https://" + url.group(0)).netloc.lower()
    if "ebay." in host:
        return ("Send the full eBay listing link (it looks like ebay.co.uk/itm/123456789012); "
                "shortened links don't include the listing number.")
    site = next((name for key, name in SITES.items() if key in host), "that site")
    return f"I can't open adverts on {site} directly. {SHARE_TIP}"


def api_lookup(registration, _client=[]):
    """Real MOT API lookup, cached for 24h in the project database."""
    from assess.store import cached_vehicle_payload  # needs the project database
    if not _client:
        _client.append(MotClient())
    return cached_vehicle_payload(normalise_registration(registration), _client[0].vehicle)


def ebay_client(_client=[]):
    """Shared eBay API client, or None when the eBay keys are not set."""
    from utils.ebay_api import EbayClient
    if not _client and EbayClient.configured():
        _client.append(EbayClient())
    return _client[0] if _client else None


def _mot_error(exc):
    if exc.code in ("MOTH-RL-01", "MOTH-RL-02"):
        return "The MOT service is rate-limiting me. Try again in a few minutes."
    return f"The MOT service returned an error ({exc.code or exc.status}). Try again later."


def assess_text(text, lookup=api_lookup, today=None, ebay=None):
    """Reply text for a free-text request (a registration, advert text and/or an eBay link)."""
    advert = parse_advert(URL_RE.sub(" ", text))  # links can contain plate-like fragments
    item_id = find_item_id(text)
    if item_id:
        client = ebay or ebay_client()
        if client is None:
            return "eBay links need the eBay API keys (EBAY_CLIENT_ID, EBAY_CLIENT_SECRET) set on this machine."
        try:
            return assess_ebay(item_id, client, lookup, registration=advert.registration, today=today)
        except MotApiError as exc:
            return _mot_error(exc)
    if not advert.registration:
        return link_reply(text) or "I couldn't find a registration in that.\n\n" + HELP
    try:
        payload = lookup(advert.registration)
    except MotApiError as exc:
        return _mot_error(exc)
    if payload is None:
        return f"No MOT record found for {advert.registration}. Check the registration and try again."
    vehicle = vehicle_from_api(payload)
    report = build_report(vehicle, advert, run_checks(vehicle, advert, today=today))
    client = ebay or ebay_client()
    return add_market(add_repairs(report, vehicle, client), vehicle, advert.price, client)
