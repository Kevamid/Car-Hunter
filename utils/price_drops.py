"""Price-drop alerts for cars already in the database.

Each source calls drop_before_update() BEFORE upsert_listing() records the new price, sends
drop_message() if it returned a listing, and only then saves (a failed Telegram call is retried
next run, like new-car alerts). Drops smaller than MIN_DROP are recorded but not alerted.
"""
import html

from utils.db import get_listing

MIN_DROP = 100  # £


def _int(value):
    """An int from a stored value that may be text ("56,000", "4995"); None if there isn't one."""
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def drop_before_update(url, new_price, stored=None):
    """The stored listing (dict) if `new_price` is a drop worth an alert, else None."""
    new_price = _int(new_price)
    if not new_price:
        return None
    row = stored if stored is not None else get_listing(url)
    old = _int((row or {}).get("price_raw"))
    if old and new_price <= old - MIN_DROP:
        return row
    return None


def drop_message(row, new_price, link):
    """Telegram (HTML) message: title, new and old price, year/mileage, link."""
    old = _int(row["price_raw"])
    new_price = _int(new_price)
    diff = old - new_price
    miles = _int(row.get("mileage"))  # stored values can be text (e.g. "56,000"): never trust the type
    facts = " · ".join(x for x in (str(row["year"]) if row.get("year") else None,
                                   f"{miles:,} miles" if miles else None) if x)
    source = html.escape(str(row.get("source") or ""))
    link_line = (f'{source}: <a href="{html.escape(link, quote=True)}">View advert</a>' if len(link) > 100
                 else f"{source}: {link}")
    lines = [f"📉 <b>Price drop</b>: {html.escape(str(row.get('title') or ''))}",
             f"Now <b>£{new_price:,}</b>, was £{old:,} (down £{diff:,}, {round(100 * diff / old)}%)"]
    if facts:
        lines.append(facts)
    lines.append(link_line)
    return "\n".join(lines)
