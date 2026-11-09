"""Offline tests for assess/queue.py (dashboard "Assess this car" requests).

    python -m unittest discover -s tests
"""
import unittest

from assess.queue import request_text, run


class QueueTests(unittest.TestCase):
    def test_request_text(self):
        self.assertEqual(request_text({"registration": "AB12CDE", "price": 8995, "mileage": 74000, "year": 2009}),
                         "AB12CDE £8,995 74000 miles 2009")
        self.assertEqual(request_text({"url": "https://www.ebay.co.uk/itm/123456789012", "price": 5000}),
                         "£5,000 https://www.ebay.co.uk/itm/123456789012")
        self.assertEqual(request_text({"registration": "AB12CDE", "url": "https://www.facebook.com/marketplace/item/1"}),
                         "AB12CDE")   # other sites' links are never opened

    def test_report_saved_then_sent(self):
        saved, sent = [], []
        rows = [{"id": 1, "registration": "AB12CDE", "title": "2009 Mazda MX-5"},
                {"id": 2, "registration": "XY13ZZZ", "title": "Clio 197"}]

        def assess(text):
            if text.startswith("XY13"):
                raise RuntimeError("boom")
            return f"Report for {text}"
        handled = run(rows, assess=assess, notify=sent.append, store=lambda *a: saved.append(a))
        self.assertEqual(handled, 2)
        self.assertEqual(saved[0], (1, "done", "Report for AB12CDE"))
        self.assertEqual(saved[1][:2], (2, "error"))                       # one failure doesn't stop the rest
        self.assertTrue(sent[0].startswith("🔎 Assessment: 2009 Mazda MX-5\n\nReport for AB12CDE"))

    def test_unsaved_report_is_not_sent(self):
        sent = []

        def store(*args):
            raise OSError("down")
        self.assertEqual(run([{"id": 1, "registration": "AB12CDE"}], assess=lambda t: "r", notify=sent.append, store=store), 0)
        self.assertEqual(sent, [])                                         # retried next run, no duplicate Telegram


if __name__ == "__main__":
    unittest.main()
