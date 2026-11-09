"""Price-drop alerts for cars already in the database.

Each source calls drop_before_update() BEFORE upsert_listing() records the new price, sends
drop_message() if it returned a listing, and only then saves (a failed Telegram call is retried
next run, like new-car alerts). Drops smaller than MIN_DROP are recorded but not alerted.
"""
import html

from utils.db import get_listing

MIN_DROP = 100  # £


def drop_before_update(url, new_price, stored=None):
    """The stored listing (dict) if `new_price` is a drop worth an alert, else None."""
    if not new_price:
        return None
    row = stored if stored is not None else get_listing(url)
    if row and row.get("price_raw") and new_price <= row["price_raw"] - MIN_DROP:
        return row
    return None


def drop_message(row, new_price, link):
    """Telegram (HTML) message: title, new and old price, year/mileage, link."""
    old = row["price_raw"]
    diff = old - new_price
    facts = " · ".join(x for x in (str(row["year"]) if row.get("year") else None,
                                   f"{row['mileage']:,} miles" if row.get("mileage") else None) if x)
    source = html.escape(str(row.get("source") or ""))
    link_line = (f'{source}: <a href="{html.escape(link, quote=True)}">View advert</a>' if len(link) > 100
                 else f"{source}: {link}")
    lines = [f"📉 <b>Price drop</b>: {html.escape(str(row.get('title') or ''))}",
             f"Now <b>£{new_price:,}</b>, was £{old:,} (down £{diff:,}, {round(100 * diff / old)}%)"]
    if facts:
        lines.append(facts)
    lines.append(link_line)
    return "\n".join(lines)
