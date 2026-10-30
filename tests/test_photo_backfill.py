"""Offline tests for the photo backfill (fake eBay client, fake mailbox, no Supabase).

    python -m unittest discover -s tests
"""
import os
import unittest
from unittest import mock

from utils import photo_backfill as pb
from utils.ebay_api import EbayApiError
from utils.mailbox import Message

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


class FakeEbay:
    def __init__(self):
        self.calls = []

    def get(self, path, **params):
        self.calls.append(params["legacy_item_id"])
        if params["legacy_item_id"] == "222":
            raise EbayApiError(404, "gone")
        return {"itemId": "v1|111|0", "title": "2003 Mazda MX-5",
                "image": {"imageUrl": "https://i.ebayimg.com/1.jpg"},
                "additionalImages": [{"imageUrl": "https://i.ebayimg.com/2.jpg"}],
                "description": "<p>Lovely car</p>", "localizedAspects": [{"name": "Mileage", "value": "89,000"}]}


class BackfillTests(unittest.TestCase):
    def setUp(self):
        self.updates = []
        patcher = mock.patch.object(pb, "update_car", lambda url, fields: self.updates.append((url, fields)) or True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_ebay_fills_photos_and_marks_ended(self):
        cars = [{"url": "https://www.ebay.co.uk/itm/111", "source": "eBay", "details": {}},
                {"url": "https://www.ebay.co.uk/itm/222", "source": "eBay", "details": {}},
                {"url": "https://www.ebay.co.uk/itm/333", "source": "eBay", "details": {"listing_status": "ended"}},
                {"url": "https://www.pistonheads.com/buy/listing/1", "source": "PistonHeads", "details": {}}]
        client = FakeEbay()
        self.assertEqual(pb.backfill_ebay(cars, client), (1, 1))
        self.assertEqual(client.calls, ["111", "222"])                   # ended ones aren't retried; PH isn't eBay
        filled = dict(self.updates)["https://www.ebay.co.uk/itm/111"]
        self.assertEqual(filled["images"], ["https://i.ebayimg.com/1.jpg", "https://i.ebayimg.com/2.jpg"])
        self.assertEqual(filled["description"], "Lovely car")
        self.assertEqual(dict(self.updates)["https://www.ebay.co.uk/itm/222"], {"details": {"listing_status": "ended"}})

    def test_ebay_respects_the_limit(self):
        cars = [{"url": f"https://www.ebay.co.uk/itm/9{i}", "source": "eBay", "details": {}} for i in range(5)]
        client = FakeEbay()
        pb.backfill_ebay(cars, client, limit=2)
        self.assertEqual(len(client.calls), 2)

    def test_email_photos_matched_by_listing_url(self):
        with open(os.path.join(FIXTURES, "pistonheads_two_listings.html"), encoding="utf-8") as f:
            html = f.read().replace('src=""', 'src="https://img.pistonheads.com/x.jpg"', 1)
        message = Message("<1@ph>", "alerts@email.pistonheads.com", "New results", None, html, "")
        cars = [{"url": "https://www.pistonheads.com/buy/listing/21245034", "source": "PistonHeads"},
                {"url": "https://www.pistonheads.com/buy/listing/999", "source": "PistonHeads"}]   # not in any email
        self.assertEqual(pb.backfill_email(cars, fetch=lambda senders, days: [message]), 1)
        self.assertEqual(self.updates, [("https://www.pistonheads.com/buy/listing/21245034",
                                         {"images": ["https://img.pistonheads.com/x.jpg"]})])


if __name__ == "__main__":
    unittest.main()
