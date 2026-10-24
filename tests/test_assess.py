"""Offline tests for the advert assessment (no network).

    python -m unittest discover -s tests
"""
import io
import json
import os
import unittest
import urllib.error
from datetime import date
from unittest import mock

from assess import mot
from assess.checks import AMBER, GREEN, INFO, RED, run_checks
from assess.parse import parse_advert
from assess.report import build_report
from assess.vehicle import vehicle_from_api

SAMPLES = os.path.join(os.path.dirname(__file__), "..", "assess", "samples")
TODAY = date(2026, 10, 18)


def load(name):
    with open(os.path.join(SAMPLES, name), encoding="utf-8") as f:
        return vehicle_from_api(json.load(f))


def findings(vehicle, **advert):
    return run_checks(vehicle, parse_advert("", **advert), today=TODAY)


def has(found, severity, fragment):
    return any(f.severity == severity and fragment.lower() in f.message.lower() for f in found)


class ParseTests(unittest.TestCase):
    def test_plates(self):
        for text, plate in [("2009 MX-5 reg AB09 CDE", "AB09CDE"), ("R123 ABC for sale", "R123ABC"),
                            ("ABC 123D", "ABC123D")]:
            self.assertEqual(parse_advert(text).registration, plate)

    def test_no_false_plate_from_model_names(self):
        self.assertIsNone(parse_advert("Renault Clio RS 200 Cup, M140i, GT86").registration)

    def test_claims(self):
        ad = parse_advert("2007 Porsche Cayman 2.7, Silver, petrol, 74k miles, £14,490 ono. Tax £35.")
        self.assertEqual((ad.year, ad.colour, ad.fuel, ad.engine_litres, ad.mileage, ad.price),
                         (2007, "silver", "petrol", 2.7, 74000, 14490))

    def test_explicit_values_win(self):
        self.assertEqual(parse_advert("74,000 miles", mileage=80000).mileage, 80000)


class VehicleTests(unittest.TestCase):
    def test_names_and_order(self):
        v = load("sample_mx5_clean.json")
        self.assertEqual((v.make, v.model), ("Mazda", "MX-5"))
        self.assertEqual(v.tests, sorted(v.tests, key=lambda t: t.date))

    def test_km_and_unreadable(self):
        v = vehicle_from_api({"motTests": [
            {"completedDate": "2020-01-01T00:00:00Z", "testResult": "PASSED", "odometerValue": "10000",
             "odometerUnit": "KM", "odometerResultType": "READ", "dataSource": "DVSA"},
            {"completedDate": "2021-01-01T00:00:00Z", "testResult": "PASSED", "odometerValue": None,
             "odometerResultType": "UNREADABLE", "dataSource": "DVSA"}]})
        self.assertEqual([t.miles for t in v.tests], [6214, None])


class CheckTests(unittest.TestCase):
    def test_problem_car(self):
        found = findings(load("sample_cayman_concerns.json"), mileage=74000, colour="silver", year=2007)
        self.assertTrue(has(found, RED, "mileage went down"))
        self.assertTrue(has(found, RED, "advert says 74,000"))
        self.assertTrue(has(found, AMBER, "silver"))
        self.assertTrue(has(found, AMBER, "recall"))
        self.assertTrue(has(found, AMBER, "no mot for 39 months"))
        self.assertTrue(has(found, AMBER, "dangerous defect"))       # 2022: older than 2 years -> amber
        self.assertTrue(has(found, AMBER, "corrosion noted"))
        self.assertTrue(has(found, AMBER, "oil leak noted"))
        self.assertTrue(has(found, GREEN, "mot valid until 15 jun 2027"))
        self.assertEqual(found, sorted(found, key=lambda f: [RED, AMBER, GREEN, INFO].index(f.severity)))

    def test_clean_car(self):
        found = findings(load("sample_mx5_clean.json"), mileage=111000, colour="Silver", year=2009)
        self.assertFalse([f for f in found if f.severity in (RED, AMBER)])
        self.assertTrue(has(found, GREEN, "consistent with the mot history"))
        self.assertTrue(has(found, GREEN, "no outstanding safety recalls"))
        self.assertTrue(has(found, INFO, "failed 1 of 15"))         # fail + same-day pass = one event

    def test_new_car(self):
        found = findings(load("sample_new_car.json"))
        self.assertEqual([f.severity for f in found], [INFO])

    def test_expired_and_failed_mot(self):
        v = load("sample_mx5_clean.json")
        self.assertTrue(has(run_checks(v, parse_advert(""), today=date(2027, 6, 1)), RED, "mot expired"))
        v.tests[-1].passed = False
        self.assertTrue(has(run_checks(v, parse_advert(""), today=TODAY), RED, "latest mot"))

    def test_identity_mismatches(self):
        v = load("sample_mx5_clean.json")
        found = findings(v, year=2012, fuel="diesel", engine_litres=1.6)
        self.assertTrue(has(found, RED, "advert says 2012"))
        self.assertTrue(has(found, RED, "advert says diesel"))
        self.assertTrue(has(found, AMBER, "1.6l"))
        self.assertFalse(has(findings(v, year=2010), RED, "advert says"))  # one year out is info only

    def test_advert_text_flags(self):
        v = load("sample_mx5_clean.json")
        found = run_checks(v, parse_advert("Cat N repaired, PX to clear, engine light on"), today=TODAY)
        self.assertTrue(has(found, RED, "write-off"))
        self.assertTrue(has(found, AMBER, "trade-clearance"))
        self.assertTrue(has(found, AMBER, "warning light"))

    def test_report_renders(self):
        v = load("sample_cayman_concerns.json")
        ad = parse_advert("", mileage=74000, price=14490)
        text = build_report(v, ad, run_checks(v, ad, today=TODAY))
        self.assertIn("Verdict: 🔴", text)
        self.assertIn("Ask the seller:", text)
        self.assertIn("does NOT check write-off", text)


class ModelNoteTests(unittest.TestCase):
    def car(self, make, model, reg_date, cc, defects=()):
        tests = [{"completedDate": "2025-05-01T00:00:00Z", "testResult": "PASSED", "expiryDate": "2026-12-01",
                  "odometerValue": "60000", "odometerUnit": "MI", "odometerResultType": "READ", "dataSource": "DVSA",
                  "defects": [{"text": t, "type": "ADVISORY", "dangerous": False} for t in defects]}]
        return vehicle_from_api({"make": make, "model": model, "registrationDate": reg_date,
                                 "engineSize": str(cc), "hasOutstandingRecall": "Unknown", "motTests": tests})

    def notes(self, vehicle):
        return [f for f in run_checks(vehicle, parse_advert(""), today=TODAY) if "weak spot" in f.message]

    def test_cayman_engine_and_year_rules(self):
        s = " ".join(f.message for f in self.notes(self.car("PORSCHE", "CAYMAN S", "2006-04-01", 3387)))
        self.assertIn("Bore scoring", s)
        self.assertIn("IMS", s)
        s = " ".join(f.message for f in self.notes(self.car("PORSCHE", "CAYMAN", "2006-04-01", 2687)))
        self.assertNotIn("Bore scoring", s)
        s = " ".join(f.message for f in self.notes(self.car("PORSCHE", "CAYMAN", "2010-04-01", 2893)))
        self.assertNotIn("IMS", s)                                   # 987.2 has no IMS

    def test_mot_evidence_raises_to_amber(self):
        found = self.notes(self.car("MAZDA", "MX-5", "2002-06-01", 1840, ["Nearside sill corroded"]))
        rust = next(f for f in found if "Rust" in f.message)
        self.assertEqual(rust.severity, AMBER)
        self.assertIn("shows signs of it (2025)", rust.message)
        self.assertEqual(self.notes(self.car("MAZDA", "MX-5", "2010-06-01", 1999)), [])  # NC: no NA/NB notes

    def test_megane_rs_generations(self):
        mk3 = self.notes(self.car("RENAULT", "MEGANE RENAULTSPORT 250", "2011-05-01", 1998, ["Ball joint has slight play"]))
        s = " ".join(f.message for f in mk3)
        self.assertIn("swivel or ball joints", s)
        self.assertNotIn("Hub carrier", s)                                   # Mk2-only note
        self.assertEqual(next(f for f in mk3 if "swivel" in f.message).severity, AMBER)
        mk2 = " ".join(f.message for f in self.notes(self.car("RENAULT", "MEGANE R26", "2008-05-01", 1998)))
        self.assertIn("Hub carrier", mk2)
        self.assertEqual(self.notes(self.car("RENAULT", "MEGANE", "2011-05-01", 1998)), [])  # plain Megane: none

    def test_only_intended_models(self):
        self.assertEqual(self.notes(self.car("RENAULT", "CLIO", "2008-06-01", 1149)), [])  # not a 2.0 RS
        self.assertTrue(self.notes(self.car("RENAULT", "CLIO RENAULTSPORT", "2008-06-01", 1998)))
        self.assertTrue(self.notes(self.car("BMW", "M140I", "2017-06-01", 2998)))
        self.assertTrue(self.notes(self.car("SUBARU", "BRZ", "2014-06-01", 1998)))


class FakeEbay:
    def __init__(self, detail=None, status=None):
        self.detail, self.status, self.calls = detail, status, []

    def get(self, path, **params):
        from utils.ebay_api import EbayApiError
        self.calls.append(params)
        if self.status:
            raise EbayApiError(self.status, "nope")
        return self.detail


def listing(description="", mask=None, seller="BUSINESS", title="2007 Porsche Cayman 2.7 Manual"):
    aspects = [{"name": "Mileage", "value": "88,500"}, {"name": "Year", "value": "2007"},
               {"name": "Exterior Colour", "value": "Silver"}, {"name": "Manufacturer", "value": "Porsche"},
               {"name": "Model", "value": "Cayman"}, {"name": "Engine Size", "value": "2687"},
               {"name": "Date of 1st Registration", "value": "20070320"},
               {"name": "Number of Previous Owners", "value": "5"}, {"name": "MOT Expiry", "value": "June 2027"}]
    if mask:
        aspects.append({"name": "Reg. Mark", "value": mask})
    return {"title": title, "price": {"value": "14490.00", "currency": "GBP"}, "description": description,
            "localizedAspects": aspects, "seller": {"sellerAccountType": seller}}


class EbayLinkTests(unittest.TestCase):
    URL = "https://www.ebay.co.uk/itm/2007-porsche-cayman/316789012345?_trkparms=x"

    def setUp(self):
        import tempfile
        import utils.db as db
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")  # price checks touch the database
        with open(os.path.join(SAMPLES, "sample_cayman_concerns.json"), encoding="utf-8") as f:
            self.payload = json.load(f)
        self.looked_up = []

    def lookup(self, reg):
        self.looked_up.append(reg)
        return self.payload

    def assess(self, text, ebay):
        from assess.service import assess_text
        return assess_text(text, lookup=self.lookup, today=TODAY, ebay=ebay)

    def test_item_id(self):
        from assess.ebay_link import find_item_id
        self.assertEqual(find_item_id(self.URL), "316789012345")
        self.assertEqual(find_item_id("https://www.ebay.co.uk/itm/316789012345"), "316789012345")
        self.assertIsNone(find_item_id("https://ebay.us/m/AbC123"))

    def test_dealer_plate_in_description(self):
        ebay = FakeEbay(listing("<p>Reg: <b>SA07 MPL</b>. FSH, 2 keys.</p>", mask="**07 ***"))
        reply = self.assess(self.URL, ebay)
        self.assertEqual(ebay.calls, [{"legacy_item_id": "316789012345"}])
        self.assertEqual(self.looked_up, ["SA07MPL"])
        self.assertIn("eBay listing: 2007 Porsche Cayman 2.7 Manual · £14,490", reply)
        self.assertIn("Dealer · 5 previous owners · MOT to June 2027", reply)
        self.assertIn("Registration (found in the listing): SA07MPL", reply)
        self.assertIn("Advert says silver, the record says blue", reply)   # claims taken from the listing

    def test_plate_must_fit_the_mask(self):
        ebay = FakeEbay(listing("Part-ex welcome, see also our AB65 XYZ", mask="**07 ***"))
        reply = self.assess(self.URL, ebay)
        self.assertEqual(self.looked_up, [])
        self.assertIn("doesn't show the registration", reply)

    def test_no_mask_needs_reg_context(self):
        from assess.ebay_link import registration_from_listing
        css = "<style>.x{id:AB12CDE}</style><script>var k='XY65ZZZ'</script>"
        self.assertIsNone(registration_from_listing(listing(css + "<p>Great car, FSH</p>")))   # code tokens ignored
        self.assertIsNone(registration_from_listing(listing("Ref LK09 ABC in stock")))         # no "reg" before it
        self.assertEqual(registration_from_listing(listing(css + "<p>Registration: LK09 ABC</p>")), "LK09ABC")
        self.assertIsNone(registration_from_listing(listing("Reg LK09 ABC, was reg KX07 DEF")))  # two plates: ambiguous

    def test_private_listing_without_plate(self):
        ebay = FakeEbay(listing("Lovely car, Cat N repaired, PX to clear", seller="INDIVIDUAL"))
        reply = self.assess(self.URL, ebay)
        self.assertIn("Private seller", reply)
        self.assertIn("Ask the seller for it", reply)
        self.assertIn("write-off category", reply)          # red flag from the description
        self.assertIn("Porsche Cayman (987) weak spot", reply)  # model notes from eBay's own specifics

    def test_typed_registration_wins(self):
        ebay = FakeEbay(listing("Reg SA07 MPL", mask="**07 ***"))
        reply = self.assess(f"{self.URL} KX07 ABC", ebay)
        self.assertEqual(self.looked_up, ["KX07ABC"])
        self.assertIn("Registration (you sent): KX07ABC", reply)

    def test_ended_listing(self):
        self.assertIn("may have ended", self.assess(self.URL, FakeEbay(status=404)))


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class MotClientTests(unittest.TestCase):
    def setUp(self):
        self.client = mot.MotClient("cid", "SECRET-VALUE", "KEY-VALUE", "https://login.example/tenant/oauth2/v2.0/token")
        self.calls = []

    def fake_urlopen(self, responses):
        def urlopen(request, timeout=None):
            self.calls.append(request)
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return FakeResponse(json.dumps(result).encode())
        return urlopen

    def http_error(self, status, body):
        return urllib.error.HTTPError("https://x", status, "err", {}, io.BytesIO(json.dumps(body).encode()))

    def test_lookup_sends_token_and_key(self):
        responses = [{"access_token": "TOKEN", "expires_in": 1199}, {"registration": "AB12CDE", "motTests": []}]
        with mock.patch("urllib.request.urlopen", self.fake_urlopen(responses)):
            self.assertEqual(self.client.vehicle("ab12 cde")["registration"], "AB12CDE")
        api_call = self.calls[1]
        self.assertTrue(api_call.full_url.endswith("/registration/AB12CDE"))
        self.assertEqual(api_call.get_header("Authorization"), "Bearer TOKEN")
        self.assertEqual(api_call.get_header("X-api-key"), "KEY-VALUE")

    def test_not_found_returns_none(self):
        responses = [{"access_token": "T", "expires_in": 1199}, self.http_error(404, {"errorCode": "MOTH-NF-01"})]
        with mock.patch("urllib.request.urlopen", self.fake_urlopen(responses)):
            self.assertIsNone(self.client.vehicle("AB12CDE"))

    def test_errors_do_not_leak_credentials(self):
        responses = [self.http_error(401, {"error": "invalid_client"})]
        with mock.patch("urllib.request.urlopen", self.fake_urlopen(responses)):
            with self.assertRaises(mot.MotApiError) as ctx:
                self.client.vehicle("AB12CDE")
        self.assertNotIn("SECRET-VALUE", str(ctx.exception))
        self.assertNotIn("KEY-VALUE", str(ctx.exception))


class BotTests(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(SAMPLES, "sample_cayman_concerns.json"), encoding="utf-8") as f:
            self.payload = json.load(f)
        self.sent = []

    def send(self, text, chat_id):
        self.sent.append((chat_id, text))

    def update(self, text, chat_id=111):
        return {"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text}}

    def handle(self, text, chat_id=111, lookup=None):
        from assess.bot import handle_update
        return handle_update(self.update(text, chat_id), {"111"}, lookup or (lambda reg: self.payload), self.send)

    def test_assesses_allowed_chat(self):
        reply = self.handle("SA12MPL £14,490 74k miles silver")
        self.assertIn("Verdict:", reply)
        self.assertTrue(all(chat == "111" for chat, _ in self.sent))

    def test_ignores_other_chats(self):
        self.assertIsNone(self.handle("SA12MPL", chat_id=999))
        self.assertEqual(self.sent, [])

    def test_help_and_missing_registration(self):
        self.assertIn("Send me a registration", self.handle("/start"))
        self.assertIn("couldn't find a registration", self.handle("is this cayman any good?"))

    def test_not_found_and_rate_limit(self):
        self.assertIn("No MOT record found", self.handle("AB12CDE", lookup=lambda reg: None))

        def limited(reg):
            raise mot.MotApiError(429, "MOTH-RL-02", "slow down")
        self.assertIn("rate-limiting", self.handle("AB12CDE", lookup=limited))

    def test_links(self):
        reply = self.handle("https://www.pistonheads.com/buy/listing/12345678")
        self.assertIn("can't open adverts on PistonHeads", reply)
        self.assertIn("full eBay listing link", self.handle("https://ebay.us/m/AbC123"))
        self.assertIn("can't open adverts on that site", self.handle("https://example.com/car/AB12CDE-ish"))
        # a registration alongside the link wins; plate-like text inside the link is ignored
        self.assertIn("Verdict:", self.handle("AB12CDE https://www.gumtree.com/p/toyota/x/1803657854"))
        self.assertNotIn("Verdict:", self.handle("https://www.carandclassic.com/l/C2199321AB12CDE"))

    def test_split_message(self):
        from assess.bot import split_message
        chunks = split_message("\n".join(["x" * 300] * 30), limit=1000)
        self.assertTrue(all(len(c) <= 1000 for c in chunks))
        self.assertEqual("".join(chunks).count("x"), 300 * 30)


if __name__ == "__main__":
    unittest.main()
