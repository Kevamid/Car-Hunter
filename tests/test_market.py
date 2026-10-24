"""Offline tests for price against market (fake eBay client, temporary database).

    python -m unittest discover -s tests
"""
import os
import tempfile
import unittest

import utils.db as db
from utils.market import add_market, compare, config_car_for, market_line, market_listings, price_check

MX5 = {"make": "Mazda", "model": "MX-5", "max_price": 10000}
CLIO = {"make": "Renault", "model": "Clio Sport", "aliases": ["172", "182", "197", "200", "rs"]}


def summary(title, price, buying=("FIXED_PRICE",)):
    return {"title": title, "price": {"value": str(price)}, "buyingOptions": list(buying)}


class FakeSearch:
    def __init__(self, results):
        self.results, self.calls = results, 0

    def search(self, q, filter=None, **kw):
        self.calls += 1
        self.q, self.filter = q, filter
        return self.results


MX5_MARKET = [summary(f"{y} Mazda MX-5 1.8i", p) for y, p in
              [(2010, 6500), (2011, 7000), (2011, 7200), (2012, 7500), (2011, 6900), (2010, 6800), (2012, 8000),
               (2005, 3000), (2016, 14000)]] + [summary("2011 Mazda MX-5 auction", 2000, ("AUCTION",)),
                                                 summary("2011 Mazda 3 hatch", 4000)]


class MarketTests(unittest.TestCase):
    def setUp(self):
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        db.init_db()

    def test_comparables_and_cache(self):
        client = FakeSearch(MX5_MARKET)
        comps = market_listings(client, MX5)
        self.assertEqual(len(comps), 9)                       # auction and "Mazda 3" dropped
        self.assertEqual(client.q, "Mazda MX-5")
        self.assertIn("itemLocationCountry:GB", client.filter)
        market_listings(client, MX5)
        self.assertEqual(client.calls, 1)                     # second call served from the 24h cache

    def test_compare_by_year(self):
        comps = market_listings(FakeSearch(MX5_MARKET), MX5)
        r = compare(6200, 2011, comps)                        # 2010-2012: 7 comparables, median 7000
        self.assertEqual((r["n"], r["median"], r["diff"], r["years"]), (7, 7000, -800, (2010, 2012)))
        self.assertIsNone(compare(6200, None, comps))         # no year (e.g. Autotrader): no verdict
        self.assertIsNone(compare(14000, 2016, comps))        # only 1 comparable even at +/-2 years

    def test_same_engine_first(self):
        results = [summary(f"2017 Mazda MX-5 {e} SE-L", p) for e, p in
                   [("1.5", 9000), ("1.5", 9500), ("1.5", 9800), ("1.5", 10000), ("1.5", 10200), ("1.5", 9900),
                    ("2.0", 14000), ("2.0", 14500), ("2.0", 15000), ("2.0", 13900)]]
        comps = market_listings(FakeSearch(results), MX5)
        r = compare(9350, 2017, comps, engine="1.5")
        self.assertEqual((r["n"], r["median"], r["engine"]), (6, 9850, "1.5"))
        self.assertIn("6 similar 1.5 eBay adverts", market_line(r))
        self.assertIn("few adverts so rough", market_line(r))
        r_any = compare(9350, 2017, comps, engine="1.8")        # no 1.8s: falls back to any engine
        self.assertEqual((r_any["n"], r_any["engine"]), (10, None))

    def test_line_wording(self):
        self.assertIn("About £800 below the typical asking price (median £7,000 across 7 similar eBay adverts, 2010–2012",
                      market_line({"n": 7, "median": 7000, "low": 6800, "high": 7500, "diff": -800, "years": (2010, 2012)}))
        self.assertIn("About the typical asking price",
                      market_line({"n": 7, "median": 7000, "low": 6800, "high": 7500, "diff": 200, "years": (2010, 2012)}))
        self.assertIsNone(market_line(None))

    def test_aliases_keep_it_rs_only(self):
        results = [summary(f"2008 Renault Clio {t}", p) for t, p in
                   [("197", 5000), ("RS 197", 5200), ("197 Cup", 4800), ("200", 5500), ("182 Trophy", 6000),
                    ("RS 200", 5300), ("1.2 Dynamique", 1500), ("1.5 dCi", 1800)]]
        comps = market_listings(FakeSearch(results), CLIO)
        self.assertEqual(len(comps), 6)
        self.assertNotIn(1500, [c["price"] for c in comps])

    def test_price_check_never_raises(self):
        class Broken:
            def search(self, *a, **k):
                raise RuntimeError("eBay down")
        self.assertIsNone(price_check(Broken(), MX5, 6200, 2011))
        self.assertIsNone(price_check(None, MX5, 6200, 2011))

    def test_report_uses_config_car(self):
        self.assertIs(config_car_for("Renault", "CLIO RENAULTSPORT 197", [MX5, CLIO]), CLIO)
        self.assertEqual(config_car_for("Ford", "FIESTA", [MX5, CLIO]), {"make": "Ford", "model": "FIESTA"})

        class V:
            make, model, year = "Mazda", "MX-5", 2011
        report = add_market("🚗 Car\n\nVerdict\n\nBased on DVSA MOT records.", V(), 6200, FakeSearch(MX5_MARKET), [MX5])
        self.assertIn("💷 About £800 below", report)
        self.assertTrue(report.endswith("Based on DVSA MOT records."))


if __name__ == "__main__":
    unittest.main()
