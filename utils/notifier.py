"""Telegram alerts shared by every scraper, so all sources look the same."""
import html
import os
import urllib.error
import urllib.request
from urllib.parse import urlencode


def format_alert(item):
    """Telegram (HTML) message for one listing.

    `item` keys: title, price, source, url, and optionally name, mileage (int),
    transmission, fuel, location, seller_type.
    """
    esc = lambda value: html.escape(str(value))  # noqa: E731
    facts = []
    if item.get("mileage") is not None:
        facts.append(f"{item['mileage']:,} miles")
    facts += [esc(x) for x in (item.get("transmission"), item.get("fuel")) if x]
    where = " · ".join(esc(x) for x in (item.get("location"), item.get("seller_type")) if x)
    lines = [f"🚗 <b>{esc(item.get('name') or item['title'])}</b> — {esc(item['price'])}"]
    if facts:
        lines.append(" · ".join(facts))
    if where:
        lines.append(f"📍 {where}")
    if item.get("title") and item["title"].lower() not in (item.get("name") or "").lower():
        lines.append(esc(item["title"][:110]))  # skip when it only repeats the headline
    url = item["url"]
    if len(url) > 100:  # e.g. Autotrader's tracked links: show a short link instead of ~400 characters
        lines.append(f'{esc(item["source"])}: <a href="{html.escape(url, quote=True)}">View advert</a>')
    else:
        lines.append(f"{esc(item['source'])}: {url}")
    return "\n".join(lines)


def send_telegram(text, chat_id=None, parse_mode="HTML"):
    """Send a message using the same secrets as the workflow.

    chat_id defaults to TELEGRAM_CHAT_ID; parse_mode=None sends plain text.
    Returns True (sent), False (tried and failed) or None (Telegram not configured).
    Never prints the exception text: urllib errors can embed the request URL,
    which contains the bot token.
    """
    fields = {"text": text}
    if parse_mode:
        fields["parse_mode"] = parse_mode
    return _telegram_post("sendMessage", fields, chat_id)


def send_telegram_photo(photo_url, caption, chat_id=None):
    """Send a photo (by URL) with an HTML caption. Same return values as send_telegram.
    Telegram captions are limited to 1024 characters, so long captions are trimmed."""
    return _telegram_post("sendPhoto", {"photo": photo_url, "caption": caption[:1024], "parse_mode": "HTML"}, chat_id)


def _telegram_post(method, fields, chat_id=None):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return None
    body = urlencode({"chat_id": chat_id, **fields}).encode()
    request = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=body)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status == 200
    except urllib.error.HTTPError as exc:
        print(f"Telegram send failed: HTTP {exc.code}")
    except Exception as exc:
        print(f"Telegram send failed: {type(exc).__name__}")
    return False
