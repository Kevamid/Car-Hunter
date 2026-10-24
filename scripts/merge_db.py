"""One-off migration: fold an older database (the email workflow's `email-db` artifact) into the
main one, so merging the two workflows doesn't re-alert anything.

    python scripts/merge_db.py data/email/listings.db

Copies listings (by clean_url), processed emails (by Message-ID) and seeded searches. Existing rows
win; re-running is harmless. Missing file = nothing to do.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import utils.db as db  # noqa: E402

LISTING_COLUMNS = ("clean_url, source, external_id, make, model, variant, title, price_raw, price_formatted, "
                   "mileage, year, transmission, fuel_type, location, seller_type, first_seen_at, last_seen_at")


def merge(other_path, target_path=None):
    """Returns {table: rows copied}."""
    if not os.path.exists(other_path):
        return {}
    if target_path:
        db.DB_PATH = target_path
    db.init_db()
    conn = db.get_db_connection()
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS processed_emails (message_id TEXT PRIMARY KEY, sender TEXT, subject TEXT, "
                     "listings INTEGER, processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        conn.execute("ATTACH DATABASE ? AS other", (other_path,))
        tables = {r[0] for r in conn.execute("SELECT name FROM other.sqlite_master WHERE type = 'table'")}
        copied = {}
        statements = {
            "listings": f"INSERT OR IGNORE INTO main.listings ({LISTING_COLUMNS}) SELECT {LISTING_COLUMNS} FROM other.listings",
            "processed_emails": "INSERT OR IGNORE INTO main.processed_emails SELECT * FROM other.processed_emails",
            "seeded_searches": "INSERT OR IGNORE INTO main.seeded_searches SELECT * FROM other.seeded_searches",
        }
        for table, sql in statements.items():
            if table in tables:
                copied[table] = conn.execute(sql).rowcount
        conn.commit()
        conn.execute("DETACH DATABASE other")
        return copied
    finally:
        conn.close()


if __name__ == "__main__":
    result = merge(sys.argv[1])
    print(f"merged from {sys.argv[1]}: {result or 'nothing to merge'}")
