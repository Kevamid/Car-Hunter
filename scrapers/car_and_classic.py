import logging
import re
from urllib.parse import quote_plus
from playwright.sync_api import sync_playwright
from scrapers.base import BaseScraper
from utils.db import upsert_listing
from utils.notifier import send_telegram_alert

class CarAndClassicScraper(BaseScraper):
    def __init__(self):
        super().__init__("Car & Classic")

    def fetch_listings(self, car_config: dict) -> list:
        make = car_config.get("make", "").strip().lower()
        model = car_config.get("model", "").strip()
        max_price = int(car_config.get("max_price", 0))
        aliases = car_config.get("aliases", [])

        search_url = f"https://www.carandclassic.com/search?make={quote_plus(make)}"
        if model:
            search_url += f"&query={quote_plus(model)}"
        if max_price > 0:
            search_url += f"&price_to={max_price}"

        self.logger.info(f"Targeting Car & Classic URL: {search_url}")
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
                    const links = document.querySelectorAll('a[href*="/car/"], a[href*="/auctions/"]');
                    const seenUrls = new Set();

                    links.forEach(a => {
                        const href = a.href;
                        if (seenUrls.has(href)) return;
                        seenUrls.add(href);

                        let container = a.closest('article') || a.closest('div[class*="card"]') || a.parentElement;
                        const cardText = container ? container.innerText : a.innerText;

                        let headingEl = container ? container.querySelector('h1, h2, h3, h4, [class*="heading"], [class*="title"]') : null;
                        let titleText = headingEl ? headingEl.innerText.trim() : "";

                        items.push({
                            url: href,
                            title: titleText || "NO_HEADING_FOUND",
                            rawText: cardText.substring(0, 300)
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

                    combined_searchable_str = f"{title} {raw_url} {raw_text}".lower()
                    combined_clean = combined_searchable_str.replace(" ", "").replace("-", "").replace("_", "")

                    if target_make not in combined_searchable_str:
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

                    year_match = re.search(r'\b(19\d{2}|20\d{2})\b', combined_searchable_str)
                    extracted_year = year_match.group(1) if year_match else "N/A"

                    mileage_match = re.search(r'([\d,]+)\s*(?:miles|mi|kilometres|km)', raw_text, re.IGNORECASE)
                    extracted_mileage = f"{mileage_match.group(1)} mi" if mileage_match else "N/A"

                    clean_url = raw_url.split("?")[0]

                    display_title = title if title != "NO_HEADING_FOUND" else f"{car_config.get('make')} {model}"

                    listing_data = {
                        "url": clean_url,
                        "source": "Car & Classic",
                        "external_id": str(persisted_id),
                        "make": car_config.get("make"),
                        "model": model,
                        "title": display_title,
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
                            title=display_title,
                            price=formatted_price,
                            url=clean_url,
                            source="Car & Classic",
                            mileage=extracted_mileage,
                            year=extracted_year
                        )
                        self.logger.info(f"✨ NEW C&C DISCOVERY: {display_title} ({formatted_price}) | Year: {extracted_year} | Mileage: {extracted_mileage} -> {clean_url}")

                self.logger.info(f"Successfully processed {len(new_listings)} new verified listings for {car_config.get('make')} {model} on Car & Classic.")

        except Exception as e:
            self.logger.error(f"Playwright scraping error for Car & Classic ({car_config.get('make')} {model}): {e}")

        return new_listings

    def scrape(self, car_config: dict) -> int:
        results = self.fetch_listings(car_config)
        return len(results)