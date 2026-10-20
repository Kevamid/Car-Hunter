"""eBay Browse and Taxonomy API client (official API, UK marketplace).

Auth: OAuth client credentials (application token, ~2 hours), renewed a minute early.
Env: EBAY_CLIENT_ID (Production App ID), EBAY_CLIENT_SECRET (Production Cert ID).
Errors never include credentials or tokens.
"""
import base64
import json
import os
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode

API = "https://api.ebay.com"
SCOPE = "https://api.ebay.com/oauth/api_scope"
MARKETPLACE = "EBAY_GB"
CARS_CATEGORY = "9801"  # Cars, Motorcycles & Vehicles > Cars (EBAY_GB tree 3, checked Oct 2026)


class EbayApiError(Exception):
    def __init__(self, status, message):
        super().__init__(f"eBay API error {status}: {message}")
        self.status = status


class EbayClient:
    def __init__(self, client_id=None, client_secret=None):
        self.client_id = (client_id or os.getenv("EBAY_CLIENT_ID") or "").strip()
        self.client_secret = (client_secret or os.getenv("EBAY_CLIENT_SECRET") or "").strip()
        self._token, self._token_expires = None, 0.0

    @staticmethod
    def configured():
        return bool(os.getenv("EBAY_CLIENT_ID") and os.getenv("EBAY_CLIENT_SECRET"))

    def _access_token(self):
        if self._token and time.time() < self._token_expires:
            return self._token
        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        request = urllib.request.Request(
            f"{API}/identity/v1/oauth2/token",
            data=urlencode({"grant_type": "client_credentials", "scope": SCOPE}).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded", "Authorization": f"Basic {basic}"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            raise EbayApiError(exc.code, "could not get an application token; check EBAY_CLIENT_ID / "
                                         "EBAY_CLIENT_SECRET are the Production keys") from None
        self._token = data["access_token"]
        self._token_expires = time.time() + int(data.get("expires_in", 7200)) - 60
        return self._token

    def get(self, path, **params):
        url = f"{API}{path}" + (f"?{urlencode(params)}" if params else "")
        request = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self._access_token()}",
            "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE,
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                message = (json.loads(exc.read().decode()).get("errors") or [{}])[0].get("message", "")
            except ValueError:
                message = ""
            raise EbayApiError(exc.code, message or "request failed") from None

    def search(self, q, category_ids=CARS_CATEGORY, filter=None, sort="newlyListed", limit=200):
        params = {"q": q, "category_ids": category_ids, "sort": sort, "limit": limit}
        if filter:
            params["filter"] = filter
        return self.get("/buy/browse/v1/item_summary/search", **params).get("itemSummaries", [])

    def item(self, item_id):
        """Full listing (item specifics, description, all images). item_id = 'v1|<legacy id>|0'."""
        return self.get(f"/buy/browse/v1/item/{quote(item_id, safe='')}")
