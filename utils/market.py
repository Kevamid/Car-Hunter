"""Price against the market: how a car's price compares with similar live eBay adverts.

Comparables come from one eBay search per car per day (cached 24h in `market_cache`): UK, no
auctions, same title rules as the alerts (so aliases keep it RS-only for the Clio/Megane). They
are narrowed to the car's year +/-1 (+/-2 if too few), since generation drives price. Fewer than
MIN_COMPARABLES, or no year for the car, gives no verdict rather than a misleading one.

These are ASKING prices (eBay restricts sold-price data) and mileage is not adjusted for.
"""
import json
import re
import statistics
from datetime import datetime, timedelta

from utils.dedupe import year_from_title
from utils.matching import title_matches

CACHE_AGE = timedelta(hours=24)
MIN_COMPARABLES = 6    # fewer: no verdict
FEW_COMPARABLES = 10   # fewer: verdict flagged as rough
MARKET_FILTER = "buyingOptions:{FIXED_PRICE|CLASSIFIED_AD|BEST_OFFER},itemLocationCountry:GB,price:[500..],priceCurrency:GBP"


def _query(car):
    make, words = str(car.get("make") or ""), str(car.get("model") or "").split()
    return " ".join([make] + (words[:1] if car.get("aliases") and words else words)).strip()


def _fetch(client, car):
    """[{title, price, year}] for this car from eBay (no age or price-cap filter: the whole market)."""
    model, aliases = str(car.get("model") or ""), car.get("aliases") or []
    comps = []
    for r in client.search(_query(car), filter=MARKET_FILTER):
        if "AUCTION" in (r.get("buyingOptions") or []):
            continue
        title = r.get("title") or ""
        if not title_matches(title, model.split(), model.split(), False, aliases):
            continue
        try:
            price = round(float(r["price"]["value"]))
        except (KeyError, TypeError, ValueError):
            continue
        comps.append({"title": title, "price": price, "year": year_from_title(title)})
    return comps


def market_listings(client, car):
    """Comparables for a config car, cached for 24 hours in the project database."""
    from utils.db import get_db_connection
    key = f"{car.get('make')}|{car.get('model')}|{','.join(car.get('aliases') or [])}".lower()
    conn = get_db_connection()
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS market_cache (key TEXT PRIMARY KEY, fetched_at TIMESTAMP NOT NULL, value TEXT)")
        row = conn.execute("SELECT fetched_at, value FROM market_cache WHERE key = ?", (key,)).fetchone()
        if row and datetime.now() - datetime.fromisoformat(row["fetched_at"]) < CACHE_AGE:
            return json.loads(row["value"])
        comps = _fetch(client, car)
        conn.execute("INSERT OR REPLACE INTO market_cache (key, fetched_at, value) VALUES (?, ?, ?)",
                     (key, datetime.now().isoformat(), json.dumps(comps)))
        conn.commit()
        return comps
    finally:
        conn.close()


ENGINE_RE = re.compile(r"(?<![\d.])([1-6]\.\d)(?![\d])")


def engine_from_title(title):
    """'Mazda MX-5 1.5 SE-L' -> '1.5'; None when the title doesn't say."""
    match = ENGINE_RE.search(title or "")
    return match.group(1) if match else None


def compare(price, year, comps, engine=None):
    """{n, median, low, high, diff, years, engine} or None. Same engine size first (when the car's title
    gives one and enough comparables share it), then any engine; year +/-1, then +/-2."""
    if not price or not year:
        return None
    for same_engine in ([True, False] if engine else [False]):
        for spread in (1, 2):
            prices = sorted(c["price"] for c in comps if c.get("year") and abs(c["year"] - year) <= spread
                            and (not same_engine or engine_from_title(c.get("title")) == engine))
            if len(prices) >= MIN_COMPARABLES:
                median = round(statistics.median(prices))
                return {"n": len(prices), "median": median, "low": prices[len(prices) // 4],
                        "high": prices[(3 * len(prices)) // 4], "diff": price - median,
                        "years": (year - spread, year + spread), "engine": engine if same_engine else None}
    return None


def market_line(result):
    """One plain-text line, e.g. '💷 About £800 below the typical asking price (median £7,295 across 14 ...)'."""
    if not result:
        return None
    diff, median = result["diff"], result["median"]
    if abs(diff) <= 0.05 * median:
        verdict = "About the typical asking price"
    else:
        verdict = f"About £{abs(diff):,} {'below' if diff < 0 else 'above'} the typical asking price"
    lo, hi = result["years"]
    which = f"{result['engine']} " if result.get("engine") else ""
    few = ", few adverts so rough" if result["n"] < FEW_COMPARABLES else ""
    return (f"💷 {verdict} (median £{median:,} across {result['n']} similar {which}eBay adverts, {lo}–{hi}{few}; "
            f"asking prices, mileage not adjusted)")


def config_car_for(make, model, cars):
    """The config car a vehicle belongs to (its title rules + aliases), else a plain make/model search."""
    text = f"{make} {model}"
    for car in cars or []:
        words = str(car.get("model") or "").split()
        if str(car.get("make") or "").lower() == str(make or "").lower() and \
                title_matches(text, words, words, False, car.get("aliases") or []):
            return car
    return {"make": make, "model": model}


def add_market(report, vehicle, price, client, cars=None):
    """Insert the market line above the report's footer. Never fails the report."""
    if cars is None:
        try:
            with open("config.json") as f:
                cars = json.load(f)
        except (OSError, ValueError):
            cars = []
    engine = f"{vehicle.engine_cc / 1000:.1f}" if getattr(vehicle, "engine_cc", None) else None
    line = price_check(client, config_car_for(vehicle.make, vehicle.model, cars), price, vehicle.year, engine)
    if not line:
        return report
    body, sep, footer = report.rpartition("\n\n")
    return f"{body}\n\n{line}{sep}{footer}" if sep else f"{report}\n\n{line}"


def price_check(client, car, price, year, title=None):
    """market_line for a car, or None. Never raises: pricing is a bonus, not a reason to fail an alert.
    `title` (optional) lets it compare like-for-like engine sizes."""
    if client is None or not price or not year or car is None:
        return None
    try:
        return market_line(compare(price, year, market_listings(client, car), engine_from_title(title)))
    except Exception as exc:
        print(f"Market price check skipped: {type(exc).__name__}")
        return None
