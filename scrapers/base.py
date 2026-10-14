import logging
import re

class BaseScraper:
    def __init__(self, scraper_name: str):
        self.name = scraper_name
        self.logger = logging.getLogger(scraper_name)

    def is_written_off(self, title: str, description: str, custom_exclusions: list = None) -> bool:
        default_terms = [
            "cat s", "cat n", "cat c", "cat d", "cat a", "cat b",
            "category s", "category n", "category c", "category d",
            "salvage", "write off", "written off", "write-off", "spares or repair"
        ]

        if custom_exclusions:
            default_terms.extend([k.lower() for k in custom_exclusions])

        combined_text = f"{title} {description}".lower()

        for term in set(default_terms):
            pattern = r'\b' + re.escape(term) + r'\b'
            if re.search(pattern, combined_text):
                return True

        return False

    def fetch_listings(self, car_config: dict) -> list:
        raise NotImplementedError("Subclasses must implement fetch_listings()")