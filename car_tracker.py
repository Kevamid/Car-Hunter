import asyncio
import sqlite3
import re
import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = "8928164216:AAErwgwGyvPFShdr2pPkKDQVJQlzepg7A68"
TELEGRAM_CHAT_ID = "7040962786"
TARGET_URL = "https://www.pistonheads.com/buy/search?category=used-cars&sort=price-asc"

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect("seen_listings.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS listings (
            id TEXT PRIMARY KEY,
            title TEXT,
            price TEXT,
            link TEXT
        )
    """)
    conn.commit()
    conn.close()

def is_new_listing(listing_id):
    conn = sqlite3.connect("seen_listings.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM listings WHERE id = ?", (listing_id,))
    data = cursor.fetchone()
    conn.close()
    return data is None

def save_listing(listing_id, title, price, link):
    conn = sqlite3.connect("seen_listings.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO listings VALUES (?, ?, ?, ?)", (listing_id, title, price, link))
    conn.commit()
    conn.close()

# --- TELEGRAM NOTIFIER ---
def send_telegram_alert(title, price, link):
    # Don't try to send via Telegram if placeholders are still present
    if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        return

    message = (
        f"🚨 **NEW CAR ALERT**\n\n"
        f"**Vehicle:** {title}\n"
        f"**Price:** {price}\n"
        f"**Link:** [View Listing]({link})"
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        print(f"[-] Failed to send Telegram alert: {e}")

# --- UPDATED SCRAPER ENGINE (STEP 2 INTEGRATED) ---
async def fetch_and_parse():
    async with async_playwright() as p:
        # Set headless=False so you can see the browser run live while testing
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        print("[+] Fetching listings...")
        await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)
        
        html = await page.content()
        await browser.close()
        
        soup = BeautifulSoup(html, "html.parser")
        
        # BROADER LINK SEARCH: Captures all links containing '/buy/listing/'
        listings = soup.find_all("a", href=re.compile(r"/buy/listing/"))
        
        new_count = 0
        for item in listings:
            href = item.get("href", "")
            if not href:
                continue
            
            # Extract unique ID from the URL path
            listing_id = href.split("/")[-1]
            
            # Pull heading or text inside the element
            heading = item.find(["h2", "h3", "h4", "span"])
            title = heading.text.strip() if heading else "Enthusiast Vehicle"
            
            # Search for price pattern inside the element
            price_match = re.search(r"£[\d,]+", item.text)
            price = price_match.group(0) if price_match else "Price N/A"
            
            link = "https://www.pistonheads.com" + href if href.startswith("/") else href

            if is_new_listing(listing_id):
                print(f"[!] New listing found: {title} - {price}")
                save_listing(listing_id, title, price, link)
                send_telegram_alert(title, price, link)
                new_count += 1
                
        print(f"[+] Scan complete. {new_count} new alerts detected.")

if __name__ == "__main__":
    init_db()
    asyncio.run(fetch_and_parse())