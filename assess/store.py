"""Cache MOT API responses in the project database (one row per registration).

Saves API calls when a car is re-checked and keeps a history of cars looked at.
"""
import json
from datetime import datetime, timedelta

from utils.db import get_db_connection

MAX_AGE = timedelta(hours=24)


def _ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mot_lookups (
            registration TEXT PRIMARY KEY,
            fetched_at TIMESTAMP NOT NULL,
            payload TEXT                       -- raw API JSON; NULL = vehicle not found
        )
    """)


def cached_vehicle_payload(registration, fetch, max_age=MAX_AGE):
    """Return the cached payload if fresh, otherwise call fetch(registration) and store it."""
    conn = get_db_connection()
    try:
        _ensure_table(conn)
        row = conn.execute("SELECT fetched_at, payload FROM mot_lookups WHERE registration = ?",
                           (registration,)).fetchone()
        if row and datetime.now() - datetime.fromisoformat(row["fetched_at"]) < max_age:
            return json.loads(row["payload"]) if row["payload"] else None
        payload = fetch(registration)
        conn.execute("INSERT OR REPLACE INTO mot_lookups (registration, fetched_at, payload) VALUES (?, ?, ?)",
                     (registration, datetime.now().isoformat(), json.dumps(payload) if payload else None))
        conn.commit()
        return payload
    finally:
        conn.close()
