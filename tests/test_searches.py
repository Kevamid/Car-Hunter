"""Offline tests for utils/searches.py (fake Supabase, temporary database).

    python -m unittest discover -s tests
"""
import json
import os
import tempfile
import unittest

import utils.db as db
from utils import searches

ROWS = [{"id": 1, "make": "Mazda", "model": "MX-5", "enabled": False, "min_price": None, "max_price": 10000,
         "aliases": [], "ebay": True, "note": None},
        {"id": 2, "make": "Renault", "model": "Clio Sport", "enabled": True, "min_price": 2000, "max_price": 5500,
         "aliases": ["182", "197"], "ebay": False, "note": "RS only"}]


def fake(path):
    return ROWS if path.startswith("searches") else [{"key": "pass_through", "value": True}]


def down(path):
    raise OSError("unreachable")


class SearchTests(unittest.TestCase):
    def setUp(self):
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        self.cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self.cwd)

    def test_reads_the_dashboard(self):
        loaded = searches.load(fake)
        self.assertEqual(loaded["origin"], "dashboard")
        self.assertEqual(loaded["settings"], {"pass_through": True})
        self.assertEqual(loaded["searches"][0], {"make": "Mazda", "model": "MX-5", "enabled": False, "ebay": True,
                                                 "aliases": [], "max_price": 10000})
        self.assertEqual(loaded["searches"][1]["min_price"], 2000)
        self.assertFalse(loaded["searches"][1]["ebay"])

    def test_unreachable_uses_the_last_copy(self):
        searches.load(fake)
        loaded = searches.load(down)
        self.assertEqual(loaded["origin"], "last copy")
        self.assertFalse(loaded["searches"][0]["enabled"])    # a switched-off car stays off

    def test_no_copy_falls_back_to_config(self):
        folder = tempfile.mkdtemp()
        with open(os.path.join(folder, "config.json"), "w") as f:
            json.dump([{"make": "Toyota", "model": "GT86", "max_price": 15000}], f)
        os.chdir(folder)
        loaded = searches.load(down)
        self.assertEqual(loaded["origin"], "config.json")
        self.assertEqual(loaded["searches"], [{"make": "Toyota", "model": "GT86", "max_price": 15000,
                                               "enabled": True, "ebay": True}])


if __name__ == "__main__":
    unittest.main()
