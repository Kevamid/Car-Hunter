"""Rough parts prices for what the latest MOT flagged, from eBay UK via the official Browse API.

Each advisory is matched to a part by its wording ("Nearside Front Brake disc worn..." -> front
brake discs), then priced from NEW, Buy It Now listings by UK sellers that eBay marks as fitting
the car (compatibility filter: Car Make / Model / Cars Year). Performance parts and wrong-axle or
full-set listings are dropped; the result is the median and the middle half of prices.
Parts only: labour is never included. Corrosion, leaks and tyres are not priced (cause or size
unknown). Prices are cached for 7 days.
"""
import json
import re
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

PARTS_FILTER = "buyingOptions:{FIXED_PRICE},conditionIds:{1000},itemLocationCountry:GB"  # new, Buy It Now, UK
SKIP_TITLE = re.compile(r"drilled|grooved|dimpled|slotted|performance|coilover|lowering|uprated|\brace\b", re.I)
MIN_PRICES = 5    # fewer comparable listings than this: no estimate at all
FEW_PRICES = 12   # fewer than this: estimate shown but flagged as rough
CACHE_AGE = timedelta(days=7)


@dataclass
class Part:
    key: str
    label: str
    pattern: str      # regex on the MOT defect text; first matching part wins, so order matters
    category: str     # eBay UK category id (Vehicle Parts & Accessories > Car Parts & Accessories > ...)
    query: str
    axle: bool = True  # search "front ..." / "rear ..." when the defect says which
    exclude: str | None = None  # title words that mean a different product (e.g. pads in a disc search)


PARTS = [
    Part("droplink", "anti-roll bar links", r"anti-roll bar link", "33592", "anti roll bar drop links"),
    Part("discs", "brake discs", r"brake disc", "33564", "brake discs", exclude=r"\bpads?\b"),
    Part("pads", "brake pads", r"brake pad|brake lining", "57357", "brake pads", exclude=r"\bdiscs?\b"),
    Part("shocks", "shock absorbers", r"shock absorber", "33590", "shock absorbers",
         exclude=r"top mount|mounting|bump stop|dust cover|boot|bush"),
    Part("springs", "coil springs", r"coil spring", "33582", "coil springs"),
    Part("trackrod", "track rod end", r"track rod", "33593", "track rod end"),
    Part("bearing", "wheel bearing", r"wheel bearing", "170141", "wheel bearing"),
    Part("cvboot", "CV boot", r"constant velocity boot|cv boot", "33729", "cv boot"),
    Part("arm", "suspension arm or bush", r"suspension arm|wishbone|ball joint", "33580", "suspension arm"),
    Part("exhaust", "exhaust back box", r"exhaust", "33636", "exhaust back box", axle=False),
    Part("wipers", "wiper blades", r"wiper", "179852", "wiper blades", axle=False),
]
NOT_PRICED = [(r"corro|rust", "corrosion"), (r"oil leak|leaking oil|leak", "leaks"), (r"\btyre", "tyres")]


@dataclass
class Estimate:
    label: str      # "Front brake discs"
    mid: int
    low: int
    high: int
    count: int


def flagged_items(vehicle):
    """[(Part, axle or None)] from the latest MOT, plus not-priced themes. Nearside/offside merge."""
    if not vehicle.tests:
        return [], []
    latest = vehicle.tests[-1]
    types = ("ADVISORY", "MINOR") if latest.passed else ("ADVISORY", "MINOR", "MAJOR", "DANGEROUS", "FAIL")
    items, unpriced = [], []
    for defect in latest.defects_of(*types):
        text = defect.text.lower()
        part = next((p for p in PARTS if re.search(p.pattern, text)), None)
        if part:
            axle = (re.search(r"\b(front|rear)\b", text) or [None, None])[1] if part.axle else None
            if (part, axle) not in items:
                items.append((part, axle))
        else:
            unpriced += [name for pattern, name in NOT_PRICED if re.search(pattern, text) and name not in unpriced]
    return items, unpriced


def _prices(results, axle, exclude):
    prices = []
    for r in results:
        title = r.get("title", "")
        if SKIP_TITLE.search(title) or (exclude and re.search(exclude, title, re.I)):
            continue
        if axle:
            other = "rear" if axle == "front" else "front"
            if re.search(rf"\b{other}\b", title, re.I):  # wrong axle, or a front-and-rear set
                continue
        try:
            prices.append(float(r["price"]["value"]))
        except (KeyError, TypeError, ValueError):
            pass
    return sorted(prices)


def price_part(client, vehicle, part, axle):
    """Estimate for one part, or None if eBay doesn't give enough comparable prices."""
    from utils.ebay_api import EbayApiError
    query = f"{axle} {part.query}" if axle else part.query
    params = {"q": query, "category_ids": part.category, "filter": PARTS_FILTER, "limit": 50}
    try:
        results = client.get("/buy/browse/v1/item_summary/search", **params,
                             compatibility_filter=f"Car Make:{vehicle.make};Model:{vehicle.model};Cars Year:{vehicle.year}"
                             ).get("itemSummaries", [])
    except EbayApiError as exc:
        if exc.status != 400:  # 400 = eBay doesn't know this make/model/year for fitment: use keywords
            raise
        results = []
    if len(results) < MIN_PRICES:
        params["q"] = f"{vehicle.make} {vehicle.model} {query}"
        results = client.get("/buy/browse/v1/item_summary/search", **params).get("itemSummaries", [])
    prices = _prices(results, axle, part.exclude)
    if len(prices) < MIN_PRICES:
        return None
    label = f"{axle.capitalize()} {part.label}" if axle else part.label.capitalize()
    return Estimate(label, round(statistics.median(prices)), round(prices[len(prices) // 4]),
                    round(prices[(3 * len(prices)) // 4]), len(prices))


def _cached(key, compute):
    """7-day cache in the project database (table parts_prices)."""
    from utils.db import get_db_connection
    conn = get_db_connection()
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS parts_prices (key TEXT PRIMARY KEY, fetched_at TIMESTAMP NOT NULL, value TEXT)")
        row = conn.execute("SELECT fetched_at, value FROM parts_prices WHERE key = ?", (key,)).fetchone()
        if row and datetime.now() - datetime.fromisoformat(row["fetched_at"]) < CACHE_AGE:
            value = json.loads(row["value"])
            return Estimate(**value) if value else None
        estimate = compute()
        conn.execute("INSERT OR REPLACE INTO parts_prices (key, fetched_at, value) VALUES (?, ?, ?)",
                     (key, datetime.now().isoformat(), json.dumps(estimate.__dict__ if estimate else None)))
        conn.commit()
        return estimate
    finally:
        conn.close()


def estimate_repairs(vehicle, client, cache=True):
    """(estimates, unpriced themes) for the latest MOT's flagged items."""
    items, unpriced = flagged_items(vehicle)
    estimates = []
    for part, axle in items:
        def compute(part=part, axle=axle):
            return price_part(client, vehicle, part, axle)
        key = f"{vehicle.make}|{vehicle.model}|{vehicle.year}|{part.key}|{axle}".lower()
        estimate = _cached(key, compute) if cache else compute()
        if estimate:
            estimates.append(estimate)
    return estimates, unpriced


def add_repairs(report, vehicle, client):
    """Insert the parts-price block above the report's footer. Never fails the report: without an
    eBay client, or on any eBay error, the report is returned unchanged."""
    if client is None or not vehicle.tests:
        return report
    try:
        section = repairs_section(*estimate_repairs(vehicle, client))
    except Exception as exc:
        print(f"Repair-cost estimate skipped: {type(exc).__name__}")
        return report
    if not section:
        return report
    body, sep, footer = report.rpartition("\n\n")
    return f"{body}\n\n{section}{sep}{footer}" if sep else f"{report}\n\n{section}"


def repairs_section(estimates, unpriced):
    """Plain-text block for the report (empty string when there is nothing to say)."""
    if not estimates and not unpriced:
        return ""
    lines = ["Rough parts prices for the latest MOT advisories (new, eBay UK, before fitting):"]
    lines += [f"• {e.label}: about £{e.mid:,} (most £{e.low:,}–£{e.high:,})"
              + (f", only {e.count} listings, so rough" if e.count < FEW_PRICES else "") for e in estimates]
    if len(estimates) > 1:
        lines.append(f"Parts total: about £{sum(e.mid for e in estimates):,}, plus labour.")
    elif estimates:
        lines.append("Plus labour.")
    if estimates:
        lines.append("Listings mix single parts and pairs, so treat these as a guide for negotiating.")
    if unpriced:
        lines.append(f"Not priced: {', '.join(unpriced)} (cost depends on the cause, extent or size; get a garage quote).")
    return "\n".join(lines)
