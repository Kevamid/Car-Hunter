"""Turn PistonHeads / Car & Classic alert emails into listings, without opening any web page.

Alert emails wrap links in tracking redirects. We never follow them: the real listing URL is
usually embedded (URL-encoded) in the redirect's query string, so we look inside it. Title,
price and mileage come from the text around each listing link.

FIRST VERSION, written before seeing real alert emails: generic heuristics. Refine each
site's parser against real samples (and add them as test fixtures) before relying on it.
"""
import html as htmllib
import re
from html.parser import HTMLParser
from urllib.parse import parse_qsl, unquote, urlparse

SITE_PATTERNS = {
    "PistonHeads": (re.compile(r"https?://(?:www\.)?pistonheads\.com/buy/listing/(\d+)", re.I),
                    "https://www.pistonheads.com/buy/listing/{}"),
    "Car & Classic": (re.compile(r"https?://(?:www\.)?carandclassic\.com/(?:l|car|la|make-an-offer)/([A-Za-z0-9-]+)", re.I),
                      "https://www.carandclassic.com/l/{}"),
}
# A car's price, in £ or € (Car & Classic shows European listings in euros). Search-criteria text such as
# "Max Price £10,000" (C&C) or "Price up to £22,000" (PH) is NOT a car's price: those are removed first.
PRICE_RE = re.compile(r"([£€])\s?(\d{1,3}(?:,\d{3})+|\d{3,6})(?:\.\d{2})?")
CRITERIA_RE = re.compile(r"(?:max(?:imum)?\s+price|price\s+(?:up\s+)?to|up\s+to|under|from)\s*[£€]\s?[\d,]+", re.I)
CURRENCY = {"£": "GBP", "€": "EUR"}
MILES_RE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d{1,6})\s*(?:miles|mi)\b", re.I)


class _Links(HTMLParser):
    """Flatten HTML into a list of ("text", words) and ("link", href, anchor text) tokens."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tokens, self._href, self._anchor, self._skip = [], None, [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script"):
            self._skip += 1
        elif tag == "a":
            self._href, self._anchor = dict(attrs).get("href"), []
        elif tag in ("br", "p", "div", "tr", "td", "li", "h1", "h2", "h3", "table"):
            self.tokens.append(("text", " | "))

    def handle_endtag(self, tag):
        if tag in ("style", "script"):
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._href is not None:
            self.tokens.append(("link", self._href, " ".join(self._anchor).strip()))
            self._href = None

    def handle_data(self, data):
        if self._skip:
            return
        words = " ".join(data.split())
        if words:
            if self._href is not None:
                self._anchor.append(words)
            self.tokens.append(("text", words))


def real_url(href, site, depth=3):
    """The listing URL for `site` inside `href` (directly, or URL-encoded in a redirect), or None."""
    pattern, _ = SITE_PATTERNS[site]
    candidates = [htmllib.unescape(href or "")]
    for _ in range(depth):
        nxt = []
        for url in candidates:
            match = pattern.search(url) or pattern.search(unquote(url))
            if match:
                return match
            nxt += [v for _, v in parse_qsl(urlparse(url).query) if "http" in v]
        candidates = nxt
    return None


def parse_alert(html, text, site):
    """[listing dict] from one alert email. Each distinct listing ID becomes one listing;
    its details come from the text between its first link and the next listing's first link."""
    parser = _Links()
    parser.feed(html or (text or "").replace("\n", " | "))
    tokens = parser.tokens
    if not html and text:  # plain-text email: treat bare URLs as links
        tokens = [("link", u, "") if u.startswith("http") else ("text", u)
                  for u in re.split(r"(https?://\S+)", text.replace("\n", " | "))]

    _, canonical = SITE_PATTERNS[site]
    hits = []  # (token index, listing id, anchor text)
    for i, token in enumerate(tokens):
        if token[0] == "link":
            match = real_url(token[1], site)
            if match:
                hits.append((i, match.group(1), token[2]))

    # Layout, decided once per email: details usually come AFTER each listing's link (photo/title link,
    # then price and mileage); some layouts put them BEFORE it (title, price, then the URL). A price
    # after the last listing's link means "after".
    details_after = bool(hits) and bool(PRICE_RE.search(_join(tokens[hits[-1][0]:])))

    listings, seen = [], set()
    for n, (i, listing_id, anchor) in enumerate(hits):
        if listing_id in seen:
            continue
        seen.add(listing_id)
        if details_after:  # from its first link up to the next listing's first link
            end = next((h[0] for h in hits[n + 1:] if h[1] != listing_id), len(tokens))
            block = _join(tokens[i:end])
        else:              # from just after the previous listing's last link up to this link
            prev = max((h[0] for h in hits[:n] if h[1] != listing_id), default=-1)
            block = _join(tokens[prev + 1:i + 1])
        anchors = [h[2] for h in hits if h[1] == listing_id and len(h[2]) > 8]
        title = max(anchors, key=len) if anchors else _guess_title(block)
        price = PRICE_RE.search(block)
        miles = MILES_RE.search(block)
        listings.append({
            "source": site, "id": listing_id, "url": canonical.format(listing_id), "title": title,
            "price_value": int(price.group(2).replace(",", "")) if price else None,
            "currency": CURRENCY[price.group(1)] if price else None,
            "mileage": int(miles.group(1).replace(",", "")) if miles else None,
        })
    return listings


def _join(tokens):
    """Text of a run of tokens, minus search-criteria prices ("Max Price £10,000")."""
    return CRITERIA_RE.sub(" ", " ".join(t[1] if t[0] == "text" else t[2] for t in tokens))


def _guess_title(block):
    parts = [p.strip() for p in block.split("|") if len(p.strip()) > 8 and not PRICE_RE.fullmatch(p.strip())]
    return parts[0][:120] if parts else ""
