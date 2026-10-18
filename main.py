import json
import logging
import os
from dotenv import load_dotenv
from scrapers.pistonheads import PistonHeadsScraper
from scrapers.car_and_classic import CarAndClassicScraper
from utils.db import init_db

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

def load_car_configs(filepath="config.json"):
    if not os.path.exists(filepath):
        logging.error(f"Configuration file not found at {filepath}")
        return []
    with open(filepath, "r") as f:
        return json.load(f)

def run_pipeline():
    init_db()
    
    cars = load_car_configs()
    car_config_count = len(cars)
    logging.info(f"Loaded {car_config_count} car queries. Starting multi-platform pipeline...")
    
    if not cars:
        logging.warning("No car configurations found in config.json. Exiting pipeline.")
        return

    # Initialize scraper registry.
    # PistonHeads and Car & Classic are PAUSED (Oct 2026): their terms need prior written permission
    # for automated access, which has been requested. Re-enable a scraper only once its site agrees.
    scrapers = [
        # PistonHeadsScraper(),
        # CarAndClassicScraper(),
    ]
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