"""Offline tests for the dashboard sync (no network).

    python -m unittest discover -s tests
"""
import io
import json
import os
import tempfile
import unittest
import urllib.error
from unittest import mock

import utils.db as db
from utils import supabase_sync as ss

ENV = {"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_SERVICE_KEY": "sb_secret_TESTVALUE"}


class FakeResponse(io.BytesIO):
    status = 201

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class SyncTests(unittest.TestCase):
    def setUp(self):
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        db.init_db()
        self.requests = []

    def urlopen(self, request, timeout=None):
        self.requests.append(request)
        return FakeResponse(b"")

    def test_not_configured_is_a_no_op(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(ss.sync_all(), 0)
            self.assertFalse(ss.push_details("https://x", images=["a"]))

    def test_sync_all_upserts_basic_columns(self):
        db.upsert_listing({"url": "https://www.ebay.co.uk/itm/1", "source": "eBay", "make": "Mazda", "model": "MX-5",
                           "title": "2003 Mazda MX-5", "price_raw": 4995, "price_formatted": "£4,995", "mileage": 89000})
        with mock.patch.dict(os.environ, ENV), mock.patch("urllib.request.urlopen", self.urlopen):
            self.assertEqual(ss.sync_all(), 1)
        req = self.requests[0]
        self.assertIn("on_conflict=url", req.full_url)
        self.assertIn("columns=url,source,make", req.full_url)       # only basic columns: extras are never overwritten
        self.assertNotIn("images", req.full_url)
        self.assertIn("merge-duplicates", req.get_header("Prefer"))
        row = json.loads(req.data)[0]
        self.assertEqual((row["url"], row["price"], row["mileage"]), ("https://www.ebay.co.uk/itm/1", 4995, 89000))
        self.assertTrue(row["first_seen_at"].endswith("Z"))         # SQLite UTC timestamps marked as UTC

    def test_push_details_sends_only_what_it_has(self):
        with mock.patch.dict(os.environ, ENV), mock.patch("urllib.request.urlopen", self.urlopen):
            self.assertTrue(ss.push_details("https://x", images=["https://img/1.jpg"], market="💷 line",
                                            basics={"source": "eBay", "title": "Car"}))
        row = json.loads(self.requests[0].data)[0]
        self.assertEqual(set(row), {"url", "images", "market", "source", "title"})

    def test_failure_never_raises_or_leaks_the_key(self):
        def fail(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 500, "err", {}, io.BytesIO(b'{"message":"boom"}'))
        with mock.patch.dict(os.environ, ENV), mock.patch("urllib.request.urlopen", fail), \
                mock.patch("builtins.print") as printed:
            self.assertFalse(ss.push_details("https://x", market="m"))
        self.assertNotIn("TESTVALUE", str(printed.call_args_list))

    def test_text_from_html(self):
        html = "<style>.a{color:red}</style><p>Full <b>service</b> history</p><script>x()</script><br>2 keys &amp; V5"
        self.assertEqual(ss.text_from_html(html), "Full service history\n\n2 keys & V5")  # no CSS/JS, entities decoded


if __name__ == "__main__":
    unittest.main()
