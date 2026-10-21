"""Offline tests for repair-cost estimates (fake eBay client, temporary database).

    python -m unittest discover -s tests
"""
import os
import tempfile
import unittest

import utils.db as db
from assess.repairs import PARTS, add_repairs, estimate_repairs, flagged_items, price_part, repairs_section
from assess.vehicle import vehicle_from_api
from utils.ebay_api import EbayApiError


def car(defects, passed=True, make="MAZDA", model="MX-5"):
    return vehicle_from_api({"make": make, "model": model, "registrationDate": "2003-05-01", "hasOutstandingRecall": "No",
                             "motTests": [{"completedDate": "2026-05-01T10:00:00Z", "testResult": "PASSED" if passed else "FAILED",
                                           "odometerValue": "90000", "odometerUnit": "MI", "odometerResultType": "READ",
                                           "dataSource": "DVSA",
                                           "defects": [{"text": t, "type": ty, "dangerous": False} for ty, t in defects]}]})


def results(*pairs):
    return {"itemSummaries": [{"title": t, "price": {"value": str(p), "currency": "GBP"}} for t, p in pairs]}


class FakeParts:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def get(self, path, **params):
        self.calls.append(params)
        reply = self.responses.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


GOOD_DISCS = results(("Front brake discs pair 255mm", 60), ("Front vented discs", 70), ("Front discs MX5 NB", 71),
                     ("Front brake discs OE quality", 80), ("Front discs 270mm", 100),
                     ("Front DRILLED GROOVED discs", 300), ("Rear brake discs", 40), ("Front discs and pads kit", 140))


class RepairTests(unittest.TestCase):
    def setUp(self):
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")

    def test_flagged_items(self):
        v = car([("ADVISORY", "Nearside Front Brake disc worn, pitted or scored, but not seriously weakened"),
                 ("ADVISORY", "Offside Front Brake disc worn, pitted or scored, but not seriously weakened"),
                 ("ADVISORY", "Front Anti-roll bar linkage ball joint has slight play"),
                 ("ADVISORY", "Nearside Rear Coil spring corroded"),
                 ("ADVISORY", "Offside Front Tyre worn close to legal limit"),
                 ("ADVISORY", "Nearside Sill corroded but not seriously weakened"),
                 ("ADVISORY", "Exhaust has a minor leak of exhaust gases")])
        items, unpriced = flagged_items(v)
        self.assertEqual([(p.key, axle) for p, axle in items],
                         [("discs", "front"), ("droplink", "front"), ("springs", "rear"), ("exhaust", None)])
        self.assertEqual(unpriced, ["tyres", "corrosion"])

    def test_failed_latest_mot_includes_fail_items(self):
        v = car([("MAJOR", "Offside Front Shock absorber leaking")], passed=False)
        self.assertEqual([p.key for p, _ in flagged_items(v)[0]], ["shocks"])
        self.assertEqual(flagged_items(car([("MAJOR", "Offside Front Shock absorber leaking")]))[0], [])  # fixed at the pass

    def test_price_filters_and_median(self):
        client = FakeParts([GOOD_DISCS])
        discs = next(p for p in PARTS if p.key == "discs")
        e = price_part(client, car([]), discs, "front")
        self.assertEqual((e.label, e.count, e.mid, e.low, e.high), ("Front brake discs", 5, 71, 70, 80))
        self.assertEqual(client.calls[0]["compatibility_filter"], "Car Make:Mazda;Model:MX-5;Cars Year:2003")
        self.assertEqual(client.calls[0]["q"], "front brake discs")

    def test_unknown_fitment_falls_back_to_keywords(self):
        client = FakeParts([EbayApiError(400, "no compatibility"), GOOD_DISCS])
        discs = next(p for p in PARTS if p.key == "discs")
        e = price_part(client, car([], make="BMW", model="M140I"), discs, "front")
        self.assertEqual(e.mid, 71)
        self.assertEqual(client.calls[1]["q"], "BMW M140I front brake discs")
        self.assertNotIn("compatibility_filter", client.calls[1])

    def test_too_few_prices_gives_nothing(self):
        client = FakeParts([results(("Front discs", 50)), results(("Front discs", 55))])
        self.assertIsNone(price_part(client, car([]), next(p for p in PARTS if p.key == "discs"), "front"))

    def test_cache(self):
        v = car([("ADVISORY", "Nearside Front Brake disc worn")])
        client = FakeParts([GOOD_DISCS])
        first = estimate_repairs(v, client)
        second = estimate_repairs(v, client)          # served from the cache: no second API call
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(first[0][0].mid, second[0][0].mid)

    def test_section_and_report_insertion(self):
        v = car([("ADVISORY", "Nearside Front Brake disc worn"), ("ADVISORY", "Nearside Sill corroded")])
        report = add_repairs("🚗 Car\n\nVerdict: ok\n\nBased on DVSA MOT records.", v, FakeParts([GOOD_DISCS]))
        self.assertIn("• Front brake discs: about £71 (most £70–£80), only 5 listings, so rough\nPlus labour.\n"
                      "Listings mix single parts", report)
        self.assertIn("Not priced: corrosion", report)
        self.assertTrue(report.endswith("Based on DVSA MOT records."))   # footer stays last
        self.assertEqual(repairs_section([], []), "")

    def test_ebay_failure_leaves_report_unchanged(self):
        v = car([("ADVISORY", "Nearside Front Brake disc worn")])
        self.assertEqual(add_repairs("report", v, FakeParts([EbayApiError(500, "down")])), "report")
        self.assertEqual(add_repairs("report", v, None), "report")


if __name__ == "__main__":
    unittest.main()
