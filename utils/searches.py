"""The cars being hunted ("searches"), edited from the dashboard's Searches panel.

Read from Supabase (`searches` + `settings`, supabase/003_searches.sql) every run. Each successful read is
kept in the SQLite DB, so if Supabase can't be reached the pipeline uses the last copy it read (a switched
off car stays off), and only then config.json. Never raises.

A search is a config dict as the scrapers expect (make, model, max_price, optional min_price, aliases)
plus `enabled` (False = no alerts from any source) and `ebay` (False = not searched on eBay).
"""
import json
import os
import urllib.request

from utils.db import get_db_connection

CONFIG_FILE = "config.json"


def _get(path):
    base, key = os.getenv("SUPABASE_URL", "").rstrip("/"), os.getenv("SUPABASE_SERVICE_KEY", "")
    request = urllib.request.Request(f"{base}/rest/v1/{path}", headers={"apikey": key})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _from_row(row):
    search = {"make": row["make"], "model": row["model"], "enabled": row.get("enabled", True) is not False,
              "ebay": row.get("ebay", True) is not False, "aliases": row.get("aliases") or []}
    for field in ("max_price", "min_price"):
        if row.get(field):
            search[field] = int(row[field])
    return search


def _cache(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS search_cache (id INTEGER PRIMARY KEY CHECK (id = 1), "
                 "data TEXT NOT NULL, saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")


def _save_cache(data):
    conn = get_db_connection()
    try:
        _cache(conn)
        conn.execute("INSERT OR REPLACE INTO search_cache (id, data) VALUES (1, ?)", (json.dumps(data),))
        conn.commit()
    finally:
        conn.close()


def _load_cache():
    conn = get_db_connection()
    try:
        _cache(conn)
        row = conn.execute("SELECT data FROM search_cache WHERE id = 1").fetchone()
        return json.loads(row["data"]) if row else None
    finally:
        conn.close()


def _from_config(path=CONFIG_FILE):
    try:
        with open(path) as f:
            cars = json.load(f)
    except (OSError, ValueError):
        return []
    return [{**car, "enabled": car.get("enabled", True), "ebay": car.get("ebay", True)} for car in cars]


def load(fetch=None):
    """{"searches": [...], "settings": {...}, "origin": "dashboard" | "last copy" | "config.json"}.
    `fetch(path)` reads Supabase's REST API (default: the real one, when its env vars are set)."""
    from utils.supabase_sync import configured
    if fetch is None and configured():
        fetch = _get
    if fetch is not None:
        try:
            data = {"searches": [_from_row(r) for r in fetch("searches?select=*&order=id")],
                    "settings": {r["key"]: r["value"] for r in fetch("settings?select=key,value")}}
            try:
                _save_cache(data)
            except Exception:
                pass
            return {**data, "origin": "dashboard"}
        except Exception as exc:
            print(f"Searches: couldn't read the dashboard ({type(exc).__name__}), using the last copy")
    try:
        cached = _load_cache()
    except Exception:
        cached = None
    if cached:
        return {**cached, "origin": "last copy"}
    return {"searches": _from_config(), "settings": {}, "origin": "config.json"}
