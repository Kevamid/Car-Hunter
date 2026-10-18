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


if __name__ == "__main__":
    unittest.main()
