"""Spot the same car listed on more than one site, so it alerts once.

Sources give different details: eBay has price, mileage and year; PistonHeads and Car & Classic
emails have price and year (in the title); Autotrader emails have price and mileage. Two listings
are the same car when they belong to the same config car, come from different sites, were seen
within WINDOW_DAYS, have (nearly) the same price, AND something confirms it: matching mileage, or
matching year with similar titles, or very similar titles. A conflicting mileage or year rules it out.
"""
import re

from utils.db import get_db_connection

WINDOW_DAYS = 21
MILEAGE_SLACK = 1000
YEAR_RE = re.compile(r"\b(19[6-9]\d|20[0-4]\d)\b")
STOP = {"petrol", "diesel", "manual", "auto", "automatic", "euro", "dr", "2dr", "3dr", "5dr", "s", "the"}


def year_from_title(title):
    match = YEAR_RE.search(title or "")
    return int(match.group(1)) if match else None


def _words(title):
    words = re.findall(r"[a-z0-9.]+", (title or "").lower().replace("-", ""))
    return {w for w in words if w not in STOP and not YEAR_RE.fullmatch(w)}


def title_similarity(a, b):
    wa, wb = _words(a), _words(b)
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def price_close(a, b):
    return a is not None and b is not None and a > 0 and b > 0 and abs(a - b) <= max(50, 0.01 * max(a, b))


def same_car(a, b):
    """a, b: dicts with price, mileage, year, title (mileage/year may be None)."""
    if not price_close(a.get("price"), b.get("price")):
        return False
    ma, mb, ya, yb = a.get("mileage"), b.get("mileage"), a.get("year"), b.get("year")
    if ma and mb and abs(ma - mb) > MILEAGE_SLACK:
        return False
    if ya and yb and ya != yb:
        return False
    similar = title_similarity(a.get("title"), b.get("title"))
    return bool((ma and mb) or (ya and yb and similar >= 0.4) or similar >= 0.7)


def find_duplicate(car, listing, source):
    """An earlier listing (any other source) that is the same car, as a dict, or None.
    `listing`: price, mileage, year, title. `car`: the config car (make/model labels)."""
    price = listing.get("price")
    if not price:
        return None
    slack = max(50, int(0.01 * price))
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT source, clean_url, title, price_raw, mileage, year FROM listings "
            "WHERE make = ? AND model = ? AND source != ? AND price_raw BETWEEN ? AND ? "
            "AND first_seen_at >= datetime('now', ?) ORDER BY first_seen_at",
            (car.get("make"), car.get("model"), source, price - slack, price + slack, f"-{WINDOW_DAYS} days"),
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        other = {"price": row["price_raw"], "mileage": row["mileage"],
                 "year": row["year"] or year_from_title(row["title"]), "title": row["title"]}
        if same_car(listing, other):
            return {"source": row["source"], "url": row["clean_url"], "title": row["title"], "price": row["price_raw"]}
    return None


def duplicate_note(duplicate, source, title, price, link):
    """One-line Telegram note (HTML) for a car already alerted from another site."""
    import html
    shown = f'<a href="{html.escape(link, quote=True)}">View advert</a>' if len(link) > 100 else link
    return (f"↪ Same car as the £{duplicate['price']:,} {html.escape(duplicate['title'][:60])} "
            f"({html.escape(duplicate['source'])}) is also on {html.escape(source)}"
            f"{f' at £{price:,}' if price and price != duplicate['price'] else ''}: {shown}")
