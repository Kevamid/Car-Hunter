"""Offline tests for the email-alert source (synthetic emails, temporary database).

The emails here are SYNTHETIC stand-ins. Replace / extend them with real alert emails once
they arrive, and adjust scrapers/email_parsers.py to match.

    python -m unittest discover -s tests
"""
import os
import tempfile
import unittest
from urllib.parse import quote

import utils.db as db
from scrapers.email_alerts import EmailAlerts, match_car
from scrapers.email_parsers import parse_alert
from utils.mailbox import Message, parse_message

CARS = [{"make": "Mazda", "model": "MX-5", "max_price": 10000},
        {"make": "Renault", "model": "Clio Sport", "max_price": 5500, "aliases": ["197", "200", "rs", "renaultsport"]}]


def tracked(url):
    return f"https://click.mailer.example.com/ls/click?upn=abc&u={quote(url, safe='')}&x=1"


PH_HTML = f"""<html><head><style>.a{{color:red}} AB12CDE</style></head><body>
<h2>3 new cars match your saved search</h2>
<table><tr><td><a href="{tracked('https://www.pistonheads.com/buy/listing/19876543')}"><img src="x.jpg"></a></td>
<td><a href="{tracked('https://www.pistonheads.com/buy/listing/19876543')}">2003 Mazda MX-5 1.8i Sport</a>
<p>£4,995</p><p>89,000 miles · Manual · Petrol</p></td></tr>
<tr><td><a href="{tracked('https://www.pistonheads.com/buy/listing/19870001')}">2006 Mazda MX-5 2.0 Sport</a>
<p>£12,500</p><p>60,123 miles</p></td></tr>
<tr><td><a href="https://www.pistonheads.com/buy/listing/19870002?utm_source=alert">2008 Renault Clio 197</a>
<p>£4,200</p></td></tr></table>
<a href="{tracked('https://www.pistonheads.com/account/saved-searches')}">Manage your alerts</a></body></html>"""


def msg(html="", text="", sender="alerts@email.pistonheads.com", mid="<1@ph>", subject="New cars for you"):
    return Message(message_id=mid, sender=sender, subject=subject, received=None, html=html, text=text)


class ParserTests(unittest.TestCase):
    def test_pistonheads_tracked_links(self):
        listings = parse_alert(PH_HTML, "", "PistonHeads")
        self.assertEqual([l["id"] for l in listings], ["19876543", "19870001", "19870002"])
        first = listings[0]
        self.assertEqual(first["url"], "https://www.pistonheads.com/buy/listing/19876543")
        self.assertEqual((first["title"], first["price_value"], first["mileage"]),
                         ("2003 Mazda MX-5 1.8i Sport", 4995, 89000))
        self.assertEqual(listings[2]["price_value"], 4200)

    def test_car_and_classic_plain_text(self):
        text = ("Your search: Mazda MX-5\n1995 Mazda MX-5 NA Mk1\n£6,750\n"
                "https://www.carandclassic.com/l/C2208299\n\n2001 Mazda MX-5 NB\n£4,000\n"
                "https://www.carandclassic.com/car/C2177702\n")
        listings = parse_alert("", text, "Car & Classic")
        self.assertEqual([(l["id"], l["price_value"]) for l in listings], [("C2208299", 6750), ("C2177702", 4000)])
        self.assertEqual(listings[0]["url"], "https://www.carandclassic.com/l/C2208299")

    def test_no_listings(self):
        self.assertEqual(parse_alert("<p>Your password was changed</p>", "", "PistonHeads"), [])

    def test_raw_email(self):
        raw = (b"From: PistonHeads <alerts@email.pistonheads.com>\r\nSubject: =?utf-8?q?New_cars_=C2=A3?=\r\n"
               b"Message-ID: <abc@ph>\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<p>hello</p>")
        m = parse_message(raw)
        self.assertEqual((m.sender, m.message_id, m.subject, m.html), ("alerts@email.pistonheads.com", "<abc@ph>",
                                                                        "New cars £", "<p>hello</p>"))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        db.init_db()
        self.sent = []

    def run_with(self, messages, telegram_ok=True):
        def notify(text):
            self.sent.append(text)
            return telegram_ok
        return EmailAlerts(CARS, notifier=notify, fetch=lambda domains, days: messages, alert_delay_s=0).run()

    def test_match_car(self):
        self.assertEqual(match_car({"title": "2003 Mazda MX5 1.8", "price_value": 4995}, CARS)["model"], "MX-5")
        self.assertIsNone(match_car({"title": "2006 Mazda MX-5 2.0", "price_value": 12500}, CARS))  # over budget
        self.assertEqual(match_car({"title": "Renault Clio 197", "price_value": 4200}, CARS)["model"], "Clio Sport")
        self.assertIsNone(match_car({"title": "Renault Clio 1.2 Play", "price_value": 2000}, CARS))
        self.assertEqual(match_car({"title": "Mazda MX-5", "price_value": None}, CARS)["model"], "MX-5")  # no price: kept

    def test_alerts_once_per_listing_and_email(self):
        self.assertEqual(self.run_with([msg(PH_HTML)]), 2)       # MX-5 £4,995 and Clio 197; the £12,500 MX-5 is over budget
        self.assertEqual(len(self.sent), 2)
        self.assertIn("pistonheads.com/buy/listing/19876543", self.sent[0])
        self.assertEqual(self.run_with([msg(PH_HTML)]), 0)       # same email again: already processed
        self.assertEqual(self.run_with([msg(PH_HTML, mid="<2@ph>")]), 0)  # new email, same cars: already seen
        self.assertEqual(len(self.sent), 2)

    def test_telegram_failure_is_retried(self):
        self.assertEqual(self.run_with([msg(PH_HTML)], telegram_ok=False), 0)
        self.assertEqual(self.run_with([msg(PH_HTML)]), 2)       # email was not marked processed, so retried

    def test_unreadable_email_warns_once(self):
        bad = msg("<p>Something new on PistonHeads!</p>", subject="Weekly newsletter")
        self.run_with([bad])
        self.run_with([bad])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("had no listings I could read", self.sent[0])


if __name__ == "__main__":
    unittest.main()
