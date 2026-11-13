"""Offline tests for utils/price_drops.py.

    python -m unittest discover -s tests
"""
import unittest

from utils.price_drops import drop_before_update, drop_message


class PriceDropTests(unittest.TestCase):
    def test_text_values_from_the_database(self):
        # 13 Nov 2026: a stored mileage of "56,000" (text) crashed the email step with "Cannot specify ',' with 's'"
        row = {"price_raw": "4995", "mileage": "56,000", "year": "2006", "title": "Mazda MX-5", "source": "PistonHeads"}
        self.assertIs(drop_before_update("u", "4,500", stored=row), row)
        text = drop_message(row, 4500, "https://www.pistonheads.com/buy/listing/1")
        self.assertIn("Now <b>£4,500</b>, was £4,995 (down £495, 10%)", text)
        self.assertIn("2006 · 56,000 miles", text)

    def test_unreadable_values_are_skipped(self):
        row = {"price_raw": 4995, "mileage": "not given", "year": None, "title": "MX-5", "source": "eBay"}
        self.assertNotIn("miles", drop_message(row, 4500, "https://x"))
        self.assertIsNone(drop_before_update("u", None, stored=row))
        self.assertIsNone(drop_before_update("u", 4500, stored={"price_raw": "POA"}))


if __name__ == "__main__":
    unittest.main()
