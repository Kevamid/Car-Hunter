import logging
import re
from urllib.parse import quote_plus
from playwright.sync_api import sync_playwright
from scrapers.base import BaseScraper
from utils.db import upsert_listing
from utils.notifier import send_telegram_alert

class PistonHeadsScraper(BaseScraper):
    def __init__(self):
        super().__init__("PistonHeads")

    def fetch_listings(self, car_config: dict) -> list:
        make = car_config.get("make", "").strip()
        model = car_config.get("model", "").strip()
        max_price = int(car_config.get("max_price", 0))
        aliases = car_config.get("aliases", [])

        # Construct search query for PistonHeads keywords
        search_query = f"{make} {model}"
        search_url = f"https://www.pistonheads.com/buy/search?keywords={quote_plus(search_query)}"
        if max_price > 0:
            search_url += f"&priceTo={max_price}"

        self.logger.info(f"Targeting PistonHeads URL: {search_url}")
        new_listings = []

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
                )
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                    viewport={"width": 1280, "height": 800}
                )
                page = context.new_page()

                page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(5000)

                listings_data = page.evaluate("""() => {
                    const items = [];
                    const cards = document.querySelectorAll('a[href*="/buy/"]:not([href*="search"]), div[data-testid="listing-card"], article');
                    const seenUrls = new Set();

                    cards.forEach(card => {
                        const linkEl = card.tagName === 'A' ? card : card.querySelector('a[href*="/buy/"]');
                        if (!linkEl) return;

                        const href = linkEl.href;
                        if (seenUrls.has(href)) return;
                        seenUrls.add(href);

                        const cardText = card.innerText || "";
                        const lines = cardText.split('\\n').map(l => l.trim()).filter(l => l.length > 0);

                        let title = lines.length > 0 ? lines[0] : "PistonHeads Listing";

                        items.push({
                            url: href,
                            title: title,
                            rawText: cardText
                        });
                    });
                    return items;
                }""")

                browser.close()

                target_make = make.lower()
                target_model = model.lower()
                all_aliases = [a.lower() for a in aliases]
                all_aliases.append(target_model.replace(" ", ""))

                for item in listings_data:
                    raw_url = item.get("url", "")
                    title = item.get("title", "")
                    raw_text = item.get("rawText", "")

                    if not raw_url:
                        continue

                    parts = raw_url.rstrip("/").split("?")[0].split("/")
                    persisted_id = parts[-1] if parts else raw_url

                    combined_str = f"{title} {raw_url} {raw_text}".lower()
                    combined_clean = combined_str.replace(" ", "").replace("-", "").replace("_", "")

                    if target_make not in combined_str:
                        continue

                    # Validate model or any configured alias
                    matched_variant = any(alias.replace(" ", "") in combined_clean for alias in all_aliases)
                    if not matched_variant:
                        continue

                    price_raw = 0
                    price_match = re.search(r'[£€$]\s*([\d,]+)', raw_text)
                    formatted_price = price_match.group(0) if price_match else "Check Link"
                    if price_match:
                        price_raw = int(price_match.group(1).replace(",", ""))

                    if max_price > 0 and price_raw > max_price and price_raw > 0:
                        continue

                    year_match = re.search(r'\b(19\d{2}|20\d{2})\b', combined_str)
                    extracted_year = year_match.group(1) if year_match else "N/A"

                    mileage_match = re.search(r'([\d,]+)\s*(?:miles|mi|kilometres|km)', raw_text, re.IGNORECASE)
                    extracted_mileage = f"{mileage_match.group(1)} mi" if mileage_match else "N/A"

                    clean_url = raw_url.split("?")[0]

                    listing_data = {
                        "url": clean_url,
                        "source": "PistonHeads",
                        "external_id": str(persisted_id),
                        "make": make,
                        "model": model,
                        "title": title,
                        "price_raw": price_raw,
                        "price_formatted": formatted_price,
                        "mileage": extracted_mileage,
                        "year": extracted_year,
                        "transmission": None,
                        "fuel_type": None,
                        "location": None,
                        "seller_type": None
                    }

                    is_new = upsert_listing(listing_data)
                    if is_new:
                        new_listings.append(listing_data)
                        send_telegram_alert(
                            title=title,
                            price=formatted_price,
                            url=clean_url,
                            source="PistonHeads",
                            mileage=extracted_mileage,
                            year=extracted_year
                        )
                        self.logger.info(f"✨ NEW PH DISCOVERY: {title} ({formatted_price}) | Year: {extracted_year} | Mileage: {extracted_mileage} -> {clean_url}")

                self.logger.info(f"Successfully processed {len(new_listings)} new verified listings for {make} {model} on PistonHeads.")

        except Exception as e:
            self.logger.error(f"Playwright scraping error for PistonHeads ({make} {model}): {e}")

        return new_listings

    def scrape(self, car_config: dict) -> int:
        results = self.fetch_listings(car_config)
        return len(results)