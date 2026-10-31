import logging
from dotenv import load_dotenv
from scrapers.pistonheads import PistonHeadsScraper
from scrapers.car_and_classic import CarAndClassicScraper
from scrapers.ebay import EbayScraper
from utils.db import init_db
from utils.ebay_api import EbayClient
from utils import searches

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

def run_pipeline():
    init_db()

    # searches come from the dashboard's Searches panel (falls back to the last copy, then config.json)
    loaded = searches.load()
    cars = [car for car in loaded["searches"] if car["enabled"] and car["ebay"]]
    car_config_count = len(cars)
    logging.info(f"Loaded {car_config_count} car queries (from {loaded['origin']}; "
                 f"{len(loaded['searches']) - car_config_count} switched off or not on eBay). Starting multi-platform pipeline...")

    if not cars:
        logging.warning("No car searches switched on for eBay. Exiting pipeline.")
        return

    # Initialize scraper registry.
    # PistonHeads and Car & Classic are PAUSED (Oct 2026): their terms need prior written permission
    # for automated access, which has been requested. Re-enable a scraper only once its site agrees.
    # eBay uses its official API and runs whenever its keys are set.
    scrapers = [
        # PistonHeadsScraper(),
        # CarAndClassicScraper(),
    ]
    if EbayClient.configured():
        scrapers.append(EbayScraper())
    else:
        logging.warning("eBay skipped: EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not set.")
    if not scrapers:
        logging.warning("No scrapers enabled (PistonHeads and Car & Classic paused pending permission).")

    for scraper in scrapers:
        logging.info(f"--- Running Scraper: {scraper.name} ---")
        for idx, car in enumerate(cars, 1):
            make = car.get("make")
            model = car.get("model")
            max_price = car.get("max_price", 0)
            logging.info(f"[{idx}/{car_config_count}] Searching {scraper.name} for {make} {model} (Max £{max_price:,})...")
            
            try:
                new_count = scraper.scrape(car)
                logging.info(f"Finished {scraper.name} [{make} {model}]: Found {new_count} new listings.")
            except Exception as e:
                logging.error(f"Error in {scraper.name} for {make} {model}: {e}")

    logging.info("Multi-platform pipeline complete!")

if __name__ == "__main__":
    run_pipeline()