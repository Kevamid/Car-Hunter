import sqlite3
import os
import logging
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "listings.db")

def get_db_connection():
    """Returns a connection to the SQLite database with dict-like row access."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes the unified multi-platform listings schema."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            clean_url TEXT UNIQUE NOT NULL,
            source TEXT NOT NULL,              -- e.g., 'PistonHeads', 'AutoTrader', 'eBay'
            external_id TEXT,                  -- Platform specific advert ID
            make TEXT NOT NULL,
            model TEXT NOT NULL,
            variant TEXT,                      -- e.g., 'Shadow Edition', 'Clubsport'
            title TEXT NOT NULL,
            price_raw INTEGER NOT NULL,        -- Numeric price for filtering/sorting
            price_formatted TEXT NOT NULL,     -- e.g., '£18,495'
            mileage INTEGER,                   -- Mileage integer
            year INTEGER,                      -- Model year
            transmission TEXT,                 -- 'Manual', 'Auto'
            fuel_type TEXT,                    -- 'Petrol', 'Diesel'
            location TEXT,                     -- Seller location / postcode town
            seller_type TEXT,                  -- 'Private', 'Trade'
            first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_curated_candidate BOOLEAN DEFAULT 0, -- Flagged for newsletter / sourcing report
            notes TEXT
        )
    """)

    # Price history table to track market movements and bargain drops
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id INTEGER NOT NULL,
            price INTEGER NOT NULL,
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (listing_id) REFERENCES listings (id)
        )
    """)

    conn.commit()
    conn.close()
    logging.info("Database schema initialized successfully.")

def is_listing_seen(clean_url: str) -> bool:
    """Checks if a listing URL already exists in the database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM listings WHERE clean_url = ?", (clean_url,))
    row = cursor.fetchone()
    conn.close()
    return row is not None

def upsert_listing(data: dict) -> bool:
    """
    Inserts a new listing or updates timestamp/price if previously seen.
    Returns True if this is a NEW listing, False if updated/existing.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    clean_url = data.get("url", "").split("?")[0]
    if not clean_url:
        conn.close()
        return False

    cursor.execute("SELECT id, price_raw FROM listings WHERE clean_url = ?", (clean_url,))
    existing = cursor.fetchone()

    if existing:
        listing_id = existing["id"]
        old_price = existing["price_raw"]
        new_price = data.get("price_raw", 0)

        # Update last seen timestamp
        cursor.execute(
            "UPDATE listings SET last_seen_at = CURRENT_TIMESTAMP WHERE id = ?",
            (listing_id,)
        )

        # Log price drop / change
        if new_price > 0 and new_price != old_price:
            cursor.execute(
                "UPDATE listings SET price_raw = ?, price_formatted = ? WHERE id = ?",
                (new_price, data.get("price_formatted", f"£{new_price}"), listing_id)
            )
            cursor.execute(
                "INSERT INTO price_history (listing_id, price) VALUES (?, ?)",
                (listing_id, new_price)
            )
            logging.info(f"📉 PRICE CHANGE detected for {clean_url}: £{old_price} -> £{new_price}")

        conn.commit()
        conn.close()
        return False  # Not a new listing

    else:
        # Insert new listing
        cursor.execute("""
            INSERT INTO listings (
                clean_url, source, external_id, make, model, variant,
                title, price_raw, price_formatted, mileage, year,
                transmission, fuel_type, location, seller_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            clean_url,
            data.get("source", "Unknown"),
            data.get("external_id", ""),
            data.get("make", ""),
            data.get("model", ""),
            data.get("variant", ""),
            data.get("title", "Untitled Listing"),
            data.get("price_raw", 0),
            data.get("price_formatted", "N/A"),
            data.get("mileage", None),
            data.get("year", None),
            data.get("transmission", None),
            data.get("fuel_type", None),
            data.get("location", None),
            data.get("seller_type", None)
        ))

        listing_id = cursor.lastrowid
        
        # Log initial price history
        if data.get("price_raw", 0) > 0:
            cursor.execute(
                "INSERT INTO price_history (listing_id, price) VALUES (?, ?)",
                (listing_id, data.get("price_raw"))
            )

        conn.commit()
        conn.close()
        return True  # New listing added!

# Backwards compatibility helper for existing scraper logic
def mark_listing_as_seen(clean_url: str, title: str, price_str: str, source: str = "Unknown"):
    import re
    numeric_price = int(re.sub(r"[^\d]", "", price_str)) if re.search(r"\d+", price_str) else 0
    upsert_listing({
        "url": clean_url,
        "title": title,
        "price_raw": numeric_price,
        "price_formatted": price_str,
        "source": source
    })