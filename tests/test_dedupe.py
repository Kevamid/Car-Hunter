"""Offline tests for cross-site duplicate detection (temporary database).

    python -m unittest discover -s tests
"""
import os
import tempfile
import unittest

import utils.db as db
from utils.dedupe import duplicate_note, find_duplicate, same_car, title_similarity, year_from_title

MX5 = {"make": "Mazda", "model": "MX-5", "max_price": 10000}


def save(source, url, title, price, mileage=None, year=None):
    db.upsert_listing({"url": url, "source": source, "external_id": url[-6:], "make": "Mazda", "model": "MX-5",
                       "title": title, "price_raw": price, "price_formatted": f"£{price:,}", "mileage": mileage,
                       "year": year})


class SameCarTests(unittest.TestCase):
    def test_rules(self):
        base = {"price": 5995, "mileage": 74548, "year": None, "title": "Mazda MX-5 1.8 Convertible 2dr Petrol Manual"}
        self.assertTrue(same_car(base, {"price": 5990, "mileage": 74600, "year": 2008, "title": "2008 MX-5 Roadster"}))  # mileage confirms
        self.assertFalse(same_car(base, {"price": 5995, "mileage": 61000, "year": None, "title": base["title"]}))       # mileage conflicts
        self.assertFalse(same_car(base, {"price": 6400, "mileage": 74548, "year": None, "title": base["title"]}))       # price too far
        # PistonHeads (year, no mileage) vs Autotrader (mileage, no year): titles must be very similar
        ph = {"price": 6495, "mileage": None, "year": 2011, "title": "Mazda MX-5 1.8i Kendo Euro 5 2dr (2011)"}
        self.assertFalse(same_car(ph, {"price": 6495, "mileage": 68000, "year": None,
                                       "title": "Mazda MX-5 2.0i Sport Tech Convertible"}))
        self.assertTrue(same_car(ph, {"price": 6495, "mileage": 68000, "year": None,
                                      "title": "Mazda MX-5 1.8i Kendo Euro 5 2dr"}))
        self.assertFalse(same_car(ph, {"price": 6495, "mileage": None, "year": 2009, "title": ph["title"]}))  # year conflicts

    def test_helpers(self):
        self.assertEqual(year_from_title("BMW 1 Series 3.0 M140i (2017)"), 2017)
        self.assertGreater(title_similarity("Mazda MX-5 1.8i Kendo", "2011 Mazda MX5 1.8i Kendo Euro 5"), 0.4)


class FindDuplicateTests(unittest.TestCase):
    def setUp(self):
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        db.init_db()

    def test_finds_other_site_only(self):
        save("Autotrader", "https://www.autotrader.co.uk/#alert-abc", "Mazda MX-5 1.8 Convertible 2dr Petrol Manual", 5999, 74548)
        listing = {"price": 5999, "mileage": 74500, "year": 2008, "title": "2008 Mazda MX-5 1.8"}
        dup = find_duplicate(MX5, listing, "eBay")
        self.assertEqual((dup["source"], dup["price"]), ("Autotrader", 5999))
        self.assertIsNone(find_duplicate(MX5, listing, "Autotrader"))           # same site: not a cross-site duplicate
        self.assertIsNone(find_duplicate({"make": "Toyota", "model": "GT86"}, listing, "eBay"))  # other config car

    def test_note(self):
        dup = {"source": "PistonHeads", "url": "u", "title": "Mazda MX-5 1.8i Kendo", "price": 6495}
        note = duplicate_note(dup, "Autotrader", "Mazda MX-5", 6450, "https://clicks.autotrader.co.uk/" + "x" * 200)
        self.assertIn("Same car as the £6,495 Mazda MX-5 1.8i Kendo (PistonHeads) is also on Autotrader at £6,450", note)
        self.assertIn(">View advert</a>", note)


if __name__ == "__main__":
    unittest.main()
