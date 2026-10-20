"""Offline tests for the eBay scraper (fake API client, temporary database).

    python -m unittest discover -s tests
"""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import utils.db as db
from scrapers.ebay import EbayScraper, enrich, listing_from_summary

NOW = datetime(2026, 10, 20, 18, 0, tzinfo=timezone.utc)


def summary(legacy, title, price, hours_old=2, buying=("CLASSIFIED_AD",), image=True):
    listed = (NOW - timedelta(hours=hours_old)).isoformat().replace("+00:00", "Z")
    return {"itemId": f"v1|{legacy}|0", "legacyItemId": legacy, "title": title,
            "price": {"value": str(price), "currency": "GBP"}, "buyingOptions": list(buying),
            "itemOriginDate": listed, "itemCreationDate": listed,
            "image": {"imageUrl": f"https://img/{legacy}.jpg"} if image else None,
            "itemLocation": {"postalCode": "SS6**", "country": "GB"}}


DETAIL = {"localizedAspects": [{"name": "Mileage", "value": "68,000"}, {"name": "Year", "value": "2006"},
                               {"name": "Fuel Type", "value": "Petrol"}, {"name": "Transmission", "value": "Manual"},
                               {"name": "Number of Previous Owners", "value": "4"},
                               {"name": "MOT Expiry", "value": "22nd April 2027"}],
          "seller": {"sellerAccountType": "INDIVIDUAL"}, "itemLocation": {"city": "Rayleigh"}}


class FakeClient:
    def __init__(self, results):
        self.results, self.searches, self.items = results, [], []

    def search(self, q, filter=None, **kw):
        self.searches.append((q, filter))
        return self.results

    def item(self, item_id):
        self.items.append(item_id)
        return DETAIL


class EbayTests(unittest.TestCase):
    def setUp(self):
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        db.init_db()
        self.sent = []

    def scraper(self, results, photo_ok=True):
        def photo(url, caption):
            self.sent.append(("photo", url, caption))
            return photo_ok
        s = EbayScraper(client=FakeClient(results), notifier=lambda t: self.sent.append(("text", t)) or True,
                        photo_notifier=photo, config={"alert_delay_s": 0})
        return s

    def fetch(self, results, car):
        s = self.scraper(results)
        return s, s.fetch_listings(car, now=NOW)

    def test_query_and_filter(self):
        s, _ = self.fetch([], {"make": "Mazda", "model": "MX-5", "max_price": 10000})
        q, f = s.client.searches[0]
        self.assertEqual(q, "Mazda MX-5")
        self.assertIn("price:[0..10000],priceCurrency:GBP", f)
        self.assertIn("buyingOptions:{FIXED_PRICE|CLASSIFIED_AD|BEST_OFFER}", f)
        s, _ = self.fetch([], {"make": "Renault", "model": "Clio Sport", "aliases": ["197"]})
        self.assertEqual(s.client.searches[0][0], "Renault Clio")   # aliases do the rest

    def test_filtering(self):
        results = [summary("1", "Mazda MX5 Nevada 1.8", 1250),
                   summary("2", "Mazda MX-5 auction", 1500, buying=("FIXED_PRICE", "AUCTION")),
                   summary("3", "Mazda MX-5 relisted", 3000, hours_old=60),
                   summary("4", "Mazda MX-5 dear", 12000),
                   summary("5", "Mazda 3 hatchback", 3000),
                   summary("6", "Mazda MX-5 Cat N repaired", 2500),
                   summary("7", "Mazda MX-5 stolen recovered", 2500)]
        _, kept = self.fetch(results, {"make": "Mazda", "model": "MX-5", "max_price": 10000})
        self.assertEqual([i["id"] for i in kept], ["1"])
        self.assertEqual(kept[0]["url"], "https://www.ebay.co.uk/itm/1")
        self.assertEqual(kept[0]["location"], "SS6")

    def test_aliases(self):
        results = [summary("1", "Renault Clio 2.0 Renaultsport 197", 4000),
                   summary("2", "Renault Clio 1.2 Dynamique", 3000),
                   summary("3", "Renault Megane RS 250", 5000)]
        _, kept = self.fetch(results, {"make": "Renault", "model": "Clio Sport", "max_price": 5500,
                                       "aliases": ["197", "200", "rs", "renaultsport"]})
        self.assertEqual([i["id"] for i in kept], ["1"])

    def test_enrich(self):
        item = enrich(listing_from_summary(summary("1", "Mazda MX5", 1250)), DETAIL)
        self.assertEqual((item["mileage"], item["year"], item["seller_type"], item["location"]),
                         (68000, 2006, "Private", "Rayleigh"))
        self.assertIn("4 previous owners", item["extra"])
        self.assertIn("MOT to 22nd April 2027", item["extra"])

    def test_seed_then_alert_with_photo(self):
        car = {"make": "Mazda", "model": "MX-5", "max_price": 10000}
        s = self.scraper([summary("1", "Mazda MX5 Nevada", 1250)])
        s.fetch_listings = lambda c, now=None: EbayScraper.fetch_listings(s, c, now=NOW)
        self.assertEqual(s.scrape(car), 0)                          # first run: seeded, silent
        self.assertEqual((self.sent, s.client.items), ([], []))     # no alerts, no detail calls

        s.client.results = [summary("1", "Mazda MX5 Nevada", 1250), summary("2", "Mazda MX-5 NB", 2995)]
        self.assertEqual(s.scrape(car), 1)                          # only the new one
        kind, url, caption = self.sent[0]
        self.assertEqual((kind, url), ("photo", "https://img/2.jpg"))
        self.assertIn("68,000 miles", caption)
        self.assertEqual(s.client.items, ["v1|2|0"])                # details fetched for new listings only

    def test_failed_photo_falls_back_to_text(self):
        car = {"make": "Mazda", "model": "MX-5"}
        db.mark_search_seeded("ebay|mazda|mx-5")
        s = self.scraper([summary("9", "Mazda MX-5", 3000)], photo_ok=False)
        s.fetch_listings = lambda c, now=None: EbayScraper.fetch_listings(s, c, now=NOW)
        self.assertEqual(s.scrape(car), 1)
        self.assertEqual([k for k, *_ in self.sent], ["photo", "text"])


if __name__ == "__main__":
    unittest.main()
