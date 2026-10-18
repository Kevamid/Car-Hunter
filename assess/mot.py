"""DVSA MOT history API client (https://documentation.history.mot.api.gov.uk/).

Auth: OAuth2 client credentials against Microsoft Entra ID (token URL, with the tenant ID,
comes in the credentials email), then every call sends `Authorization: Bearer <token>` and
`X-API-Key`. Limits: 15 requests/s average, burst 10, 500,000/day.

Environment: MOT_CLIENT_ID, MOT_CLIENT_SECRET, MOT_API_KEY, MOT_TOKEN_URL.
Errors never include credentials or tokens in their message.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode

API_URL = "https://history.mot.api.gov.uk/v1/trade/vehicles/registration/"
TOKEN_SCOPE = "https://tapi.dvsa.gov.uk/.default"
ENV_VARS = ("MOT_CLIENT_ID", "MOT_CLIENT_SECRET", "MOT_API_KEY", "MOT_TOKEN_URL")


class MotApiError(Exception):
    def __init__(self, status, code, message):
        super().__init__(f"MOT API error {status} {code}: {message}")
        self.status, self.code = status, code


def normalise_registration(text):
    return re.sub(r"[^A-Z0-9]", "", str(text).upper())


class MotClient:
    def __init__(self, client_id=None, client_secret=None, api_key=None, token_url=None):
        self.client_id = client_id or os.getenv("MOT_CLIENT_ID")
        self.client_secret = client_secret or os.getenv("MOT_CLIENT_SECRET")
        self.api_key = api_key or os.getenv("MOT_API_KEY")
        self.token_url = token_url or os.getenv("MOT_TOKEN_URL")
        self._token, self._token_expires = None, 0.0

    @staticmethod
    def configured():
        return all(os.getenv(name) for name in ENV_VARS)

    def _access_token(self):
        if self._token and time.time() < self._token_expires:
            return self._token
        body = urlencode({"grant_type": "client_credentials", "client_id": self.client_id,
                          "client_secret": self.client_secret, "scope": TOKEN_SCOPE}).encode()
        request = urllib.request.Request(self.token_url, data=body,
                                         headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            raise MotApiError(exc.code, "TOKEN", "could not get an access token; check "
                              "MOT_CLIENT_ID, MOT_CLIENT_SECRET and MOT_TOKEN_URL") from None
        self._token = data["access_token"]
        # Docs say 60 minutes but the sample shows ~20; trust expires_in and renew a minute early.
        self._token_expires = time.time() + int(data.get("expires_in", 1199)) - 60
        return self._token

    def vehicle(self, registration):
        """Raw API payload for a registration, or None if the vehicle is not found."""
        request = urllib.request.Request(API_URL + quote(normalise_registration(registration)), headers={
            "Authorization": f"Bearer {self._access_token()}",
            "X-API-Key": self.api_key,
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                info = json.loads(exc.read().decode() or "{}")
            except ValueError:
                info = {}
            code = info.get("errorCode", "")
            if exc.code == 404 and code in ("MOTH-NF-01", ""):
                return None
            raise MotApiError(exc.code, code, info.get("errorMessage", "request failed")) from None
