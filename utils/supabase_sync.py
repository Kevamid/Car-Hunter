"""Copy cars from the pipeline's SQLite database into Supabase, for the dashboard.

Two kinds of write, both upserts on the advert URL (Supabase's `cars.url` = SQLite `listings.clean_url`):
  * sync_all(): every run, the basic columns of every listing (price, mileage, last seen...). Cheap
    (hundreds of rows) and self-healing: a missed run is caught up next time.
  * push_details(): when a car is first alerted, the extras that only exist then: photos, eBay's
    description and item specifics, the price-against-market line, the tappable link, duplicates.
Columns not sent are left as they are, so the two never overwrite each other.

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY (the SECRET key: server-side only, bypasses row security).
Never raises and never prints the key: the dashboard is a bonus, not a reason to fail an alert run.

    python -m utils.supabase_sync     # full sync now
"""
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

BATCH = 200
DESCRIPTION_LIMIT = 6000


def configured():
    return bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_KEY"))


def _post(rows, columns):
    """Upsert rows into public.cars, updating only `columns` on conflict. Returns True on success."""
    base = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_KEY", "")
    url = f"{base}/rest/v1/cars?on_conflict=url&columns={','.join(columns)}"
    request = urllib.request.Request(url, data=json.dumps(rows).encode(), method="POST", headers={
        "apikey": key, "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    })
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return 200 <= response.status < 300
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode()).get("message", "")[:160]
        except Exception:
            pass
        print(f"Supabase sync failed: HTTP {exc.code} {detail}")
    except Exception as exc:
        print(f"Supabase sync failed: {type(exc).__name__}")
    return False


def _clean(row):
    return {k: v for k, v in row.items() if v is not None and v != ""}


BASIC = ["url", "source", "make", "model", "title", "price", "mileage", "year", "transmission", "fuel",
         "location", "seller_type", "first_seen_at", "last_seen_at"]
PRICE_CHANGE = ["previous_price", "price_changed_at"]  # columns from supabase/004_price_drops.sql


def _price_changes(conn):
    """{listing id: (previous price, when it changed)} for listings whose latest price differs from the one before."""
    history = {}
    for row in conn.execute("SELECT listing_id, price, recorded_at FROM price_history ORDER BY listing_id, id"):
        history.setdefault(row["listing_id"], []).append((row["price"], row["recorded_at"]))
    changes = {}
    for listing_id, prices in history.items():
        if len(prices) >= 2 and prices[-1][0] != prices[-2][0]:
            changes[listing_id] = (prices[-2][0], f"{prices[-1][1]}Z")
    return changes


def sync_all():
    """Upsert the basic columns of every SQLite listing. Returns the number of rows sent (0 if not configured)."""
    if not configured():
        return 0
    from utils.db import get_db_connection, init_db
    init_db()  # an empty or brand-new database has no tables yet
    conn = get_db_connection()
    try:
        changes = _price_changes(conn)
        listings = conn.execute(
            "SELECT id, clean_url, source, make, model, title, price_raw, mileage, year, transmission, fuel_type, "
            "location, seller_type, first_seen_at, last_seen_at FROM listings").fetchall()
    finally:
        conn.close()
    rows = [{
        "url": r["clean_url"], "source": r["source"], "make": r["make"], "model": r["model"], "title": r["title"],
        "price": r["price_raw"] or None, "mileage": _int(r["mileage"]), "year": _int(r["year"]),
        "transmission": r["transmission"], "fuel": r["fuel_type"], "location": r["location"],
        "seller_type": r["seller_type"],
        # SQLite CURRENT_TIMESTAMP is UTC without a zone: say so explicitly
        "first_seen_at": f"{r['first_seen_at']}Z" if r["first_seen_at"] else None,
        "last_seen_at": f"{r['last_seen_at']}Z" if r["last_seen_at"] else None,
        "previous_price": changes.get(r["id"], (None, None))[0],
        "price_changed_at": changes.get(r["id"], (None, None))[1],
    } for r in listings]
    # every row must carry the same keys for a bulk upsert: keep Nones here (they write NULL)
    columns = BASIC + PRICE_CHANGE
    sent = 0
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        ok = _post([{k: r[k] for k in columns} for r in batch], columns)
        if not ok and i == 0 and columns != BASIC:
            columns = BASIC  # price-change columns not added in Supabase yet: sync the rest
            print("Supabase sync: run supabase/004_price_drops.sql to show price drops on the dashboard")
            ok = _post([{k: r[k] for k in columns} for r in batch], columns)
        if ok:
            sent += len(batch)
    return sent


def _int(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def push_details(url, *, images=None, description=None, details=None, market=None, link=None, duplicate_of=None,
                 basics=None):
    """Upsert one car's extras (and optionally its basic columns, `basics` dict). Never raises."""
    if not configured():
        return False
    row = _clean({"url": url, "images": images or None, "description": description, "details": details or None,
                  "market": market, "link": link, "duplicate_of": duplicate_of, **(basics or {})})
    return _post([row], list(row))


def update_car(url, fields):
    """Update columns of a car that already exists (PATCH). Never raises; True on success."""
    if not configured() or not fields:
        return False
    base, key = os.getenv("SUPABASE_URL", "").rstrip("/"), os.getenv("SUPABASE_SERVICE_KEY", "")
    request = urllib.request.Request(
        f"{base}/rest/v1/cars?url=eq.{urllib.parse.quote(url, safe='')}", data=json.dumps(fields).encode(),
        method="PATCH", headers={"apikey": key, "Content-Type": "application/json", "Prefer": "return=minimal"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return 200 <= response.status < 300
    except Exception as exc:
        print(f"Supabase update failed: {type(exc).__name__}")
        return False


def text_from_html(fragment, limit=DESCRIPTION_LIMIT):
    """Readable text from a listing description (style/script dropped, whitespace collapsed)."""
    text = re.sub(r"<(style|script)\b.*?</\1>", " ", fragment or "", flags=re.S | re.I)
    text = re.sub(r"<\s*(br|/p|/div|/li|/h\d|/tr)\s*/?>", "\n", text, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = "\n".join(" ".join(line.split()) for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def main():
    from dotenv import load_dotenv
    load_dotenv()
    if not configured():
        print("Supabase sync skipped: SUPABASE_URL / SUPABASE_SERVICE_KEY not set.")
        return
    print(f"Supabase sync: {sync_all()} cars upserted.")


if __name__ == "__main__":
    main()
