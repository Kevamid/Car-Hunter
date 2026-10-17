"""PistonHeads scraper.

PistonHeads is a Next.js site. Every search page embeds its first results as JSON
in <script id="__NEXT_DATA__"> (an Apollo cache). Organic results live in
`searchPage.adverts`; the sponsored carousel lives in a separate list,
`searchPage.featuredAdverts`. Reading that JSON means no CSS selectors, no ID
blacklist and no guessing which cards are ads.

Search URL format (confirmed from the site):
    /buy/search?make-id=43&model-id=516&created-from-hours=48
Make and model are numeric IDs. They are read from the site's own filter lists,
so you can pass names ("Porsche", "Cayman") or hardcode the IDs.

Pagination (confirmed from a browser network capture): "View more" calls
    GET /api/graphql?operationName=SearchPage&variables={..."offset":16,"limit":17...}
          &extensions={"persistedQuery":{"sha256Hash":...}}
and the URL in the address bar never changes. We click "View more" ONCE and copy
that request (so filters, freshness and the query hash are exactly what the page
itself uses), then fetch the remaining pages directly by moving `offset`. If the
direct fetch ever fails we fall back to clicking the button repeatedly.

Note: the site's `total` includes the (up to 3) sponsored adverts, so a complete
scan has  organic_scanned + sponsored == total.
"""

import html
import json
import os
import re
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlencode, urlparse

from playwright.sync_api import sync_playwright

BASE_URL = "https://www.pistonheads.com"
SEARCH_URL = f"{BASE_URL}/buy/search"
API_PATH = "/api/graphql"
SOURCE = "PistonHeads"

# Server-side freshness filter. Offered values: 24, 48, 168.
FRESH_HOURS = 48
# Safety caps per results URL.
MAX_VIEW_MORE_CLICKS = 10   # click-based pagination (fallback)
MAX_API_PAGES = 40          # direct API pages (~17 listings each)
# How long to wait for the JSON that a "View more" click triggers.
VIEW_MORE_WAIT_S = 8

# Runs inside the page so cookies, origin and headers match the real site.
FETCH_JS = """async (url) => {
  try {
    const r = await fetch(url, {credentials: 'same-origin', headers: {
      'accept': 'application/graphql-response+json,application/json;q=0.9',
      'apollographql-client-name': 'pistonheads-web',
      'apollographql-client-version': '1.0.0',
      'content-type': 'application/json'}});
    return {status: r.status, text: await r.text()};
  } catch (e) { return {status: 0, text: String(e)}; }
}"""


def _norm(text):
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def _dedupe(seq):
    seen, out = set(), []
    for item in seq:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _deref(apollo, value):
    """Resolve an Apollo {"__ref": "Type:id"} pointer; inline objects pass through."""
    if isinstance(value, dict) and "__ref" in value:
        return apollo.get(value["__ref"], {})
    return value or {}


def _search_page(next_data):
    apollo = next_data["props"]["pageProps"]["__APOLLO_STATE__"]
    root = apollo.get("ROOT_QUERY", {})
    key = next((k for k in root if k.startswith("searchPage(")), None)
    return apollo, (root[key] if key else None)


def facet_options(next_data, facet_name):
    """Return [(label, id)] for a facet such as 'make' or 'model'."""
    _, search_page = _search_page(next_data)
    if not search_page:
        return []
    found, seen = [], set()

    def walk(node):
        if isinstance(node, dict):
            if "label" in node and node.get("value") not in ("", None):
                pair = (str(node["label"]), str(node["value"]))
                if pair not in seen:
                    seen.add(pair)
                    found.append(pair)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(search_page.get("facets", {}).get(facet_name))
    return found


def find_option_ids(options, wanted):
    """Match a name to facet ids, most precise tier first.

    1. exact label            "M140i"       -> the one M140i id
    2. label contains name    "Cayman"      -> every "Cayman 981", "718 Cayman", ...
    3. label has every word   "Clio Sport"  -> "Renaultsport Clio" (any word order)
    Returns every id in the first tier that matches (empty list if none).
    """
    target = _norm(wanted)
    if not target:
        return []
    pairs = [(value, _norm(label)) for label, value in options]

    exact = [v for v, n in pairs if n == target]
    if exact:
        return exact[:1]
    contains = _dedupe(v for v, n in pairs if target in n)
    if contains:
        return contains
    words = [_norm(w) for w in str(wanted).split() if _norm(w)]
    if len(words) > 1:
        return _dedupe(v for v, n in pairs if all(w in n for w in words))
    return []


def find_option_id(options, wanted):
    """Single-id convenience wrapper (used for makes)."""
    ids = find_option_ids(options, wanted)
    return ids[0] if len(ids) == 1 else None


def advert_to_listing(ad, apollo=None):
    """Convert one PistonHeads Advert object into our listing dict."""
    apollo = apollo or {}
    ad_id = str(ad.get("id", ""))
    spec = _deref(apollo, ad.get("specificationData"))
    seller = _deref(apollo, ad.get("seller"))
    price = ad.get("price")
    # POA / "price on application" and £0 placeholders have no usable price.
    if ad.get("isPriceOnApplication") or not isinstance(price, (int, float)) or price <= 0:
        price = None
    images = ad.get("fullSizeImageUrls") or []
    make = ad.get("makeAnalyticsName") or ""
    model = ad.get("modelAnalyticsName") or ""
    year = ad.get("year")
    return {
        "id": ad_id,
        "title": (ad.get("headline") or "").strip(),
        "name": " ".join(str(p) for p in (year, make, model) if p),  # tidy label for alerts
        "price": f"£{price:,}" if price else "POA",
        "price_value": price,
        "url": f"{BASE_URL}/buy/listing/{ad_id}",
        "date_text": "",  # PistonHeads does not expose a listing date
        "make": make,
        "model": model,
        "year": year,
        "mileage": spec.get("mileage"),
        "fuel": spec.get("fuelType"),
        "transmission": spec.get("transmissionType"),
        "body_type": spec.get("bodyType"),
        "seller_type": seller.get("sellerType"),
        "location": seller.get("location"),
        "image": images[0] if images else None,
        "source": SOURCE,
    }


def extract_listings(next_data):
    """Return (organic_listings, meta) from a parsed __NEXT_DATA__ blob.

    Pure function: no browser needed, so it can be tested against saved HTML.
    """
    apollo, search_page = _search_page(next_data)
    if search_page is None:
        return [], {"total": None, "make_applied": False, "model_applied": False, "featured": 0}

    featured_ids = {
        ref["__ref"].split(":", 1)[1]
        for ref in search_page.get("featuredAdverts", [])
        if isinstance(ref, dict) and "__ref" in ref
    }
    applied = _deref(apollo, search_page.get("appliedSearchFilters"))
    meta = {
        "total": search_page.get("total"),
        "make_applied": bool(applied.get("makeIds")),
        "model_applied": bool(applied.get("modelIds")),
        "featured": len(featured_ids),
    }

    listings = []
    for ref in search_page.get("adverts", []):
        ad = _deref(apollo, ref)
        ad_id = str(ad.get("id", ""))
        if not ad_id or ad_id in featured_ids or ad.get("isFeatured"):
            continue
        listings.append(advert_to_listing(ad, apollo))
    return listings, meta


def extract_adverts_from_json(payload):
    """Find organic Advert objects anywhere in a JSON response.

    Shape-agnostic on purpose: we walk the payload, skipping anything under
    `featuredAdverts`, so a change in the GraphQL response layout does not break it.
    """
    found = []

    def walk(node, sponsored):
        if isinstance(node, dict):
            if (node.get("__typename") == "Advert" and node.get("id") and "headline" in node
                    and not sponsored and not node.get("isFeatured")):
                found.append(advert_to_listing(node))
            for key, child in node.items():
                walk(child, sponsored or key == "featuredAdverts")
        elif isinstance(node, list):
            for child in node:
                walk(child, sponsored)

    walk(payload, False)
    return found


def request_template(url):
    """Parse a captured /api/graphql SearchPage URL into a reusable template."""
    try:
        query = parse_qs(urlparse(url).query)
        variables = json.loads(query["variables"][0])
        return {
            "variables": variables,
            "extensions": query["extensions"][0],
            "offset": int(variables["offset"]),
            "limit": int(variables["limit"]),
        }
    except Exception:
        return None


def matches_query(listing, tokens):
    """Every token must appear in the title/make/model."""
    haystack = _norm(f"{listing['title']} {listing['make']} {listing['model']}")
    return all(_norm(t) in haystack for t in tokens)


def format_alert(item):
    """Telegram (HTML) message for one listing."""
    esc = lambda value: html.escape(str(value))  # noqa: E731
    facts = []
    if item.get("mileage") is not None:
        facts.append(f"{item['mileage']:,} miles")
    facts += [esc(x) for x in (item.get("transmission"), item.get("fuel")) if x]
    where = " · ".join(esc(x) for x in (item.get("location"), item.get("seller_type")) if x)
    lines = [f"🚗 <b>{esc(item.get('name') or item['title'])}</b> — {esc(item['price'])}"]
    if facts:
        lines.append(" · ".join(facts))
    if where:
        lines.append(f"📍 {where}")
    if item.get("title") and item["title"].lower() not in (item.get("name") or "").lower():
        lines.append(esc(item["title"][:110]))  # skip when it only repeats the headline
    lines.append(f"{esc(item['source'])}: {item['url']}")
    return "\n".join(lines)


def send_telegram(text):
    """Send a message using the same secrets as the workflow.

    Returns True (sent), False (tried and failed) or None (Telegram not configured).
    Never prints the exception text: urllib errors can embed the request URL,
    which contains the bot token.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return None
    body = urlencode({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
    request = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status == 200
    except urllib.error.HTTPError as exc:
        print(f"[{SOURCE}] Telegram send failed: HTTP {exc.code}")
    except Exception as exc:
        print(f"[{SOURCE}] Telegram send failed: {type(exc).__name__}")
    return False


class PistonHeadsScraper:
    def __init__(self, config=None, notifier=None):
        self.config = config or {}
        self.name = SOURCE
        self.notifier = notifier or send_telegram   # callable(text) -> True / False / None
        self.max_alerts = self.config.get("max_alerts_per_query", 15)
        self.alert_delay_s = self.config.get("alert_delay_s", 1.1)  # Telegram: ~1 msg/s per chat
        self.fresh_hours = self.config.get("fresh_hours", FRESH_HOURS)
        self.max_clicks = self.config.get("max_view_more_clicks", MAX_VIEW_MORE_CLICKS)
        self.max_api_pages = self.config.get("max_api_pages", MAX_API_PAGES)
        self.view_more_wait_s = self.config.get("view_more_wait_s", VIEW_MORE_WAIT_S)
        self.direct_api = self.config.get("direct_api", True)
        self._makes = None          # [(label, id)] cached for the life of the scraper
        self._models = {}           # make_id -> [(label, id)]
        self._announced = False

    # ---- query handling -------------------------------------------------
    @staticmethod
    def _parse_query(query):
        """Accept a dict or a plain string.

        dict keys: make, model (names), make_id, model_id (optional, skip lookup),
        searchTerm (legacy "Make Model" string), min_price, max_price.
        """
        make = model = ""
        make_id = model_id = None
        if isinstance(query, dict):
            make = str(query.get("make") or "").strip()
            model = str(query.get("model") or "").strip()
            make_id = query.get("make_id")
            model_id = query.get("model_id")
            term = str(query.get("searchTerm") or "").strip()
            if not (make or model) and term:
                make, _, model = term.partition(" ")
        elif isinstance(query, str) and query.strip():
            make, _, model = query.strip().partition(" ")
        return make.strip(), model.strip(), make_id, model_id

    def build_url(self, make_id=None, model_id=None):
        params = []
        if make_id:
            params.append(f"make-id={make_id}")
        if model_id:
            params.append(f"model-id={model_id}")
        params.append(f"created-from-hours={self.fresh_hours}")
        return f"{SEARCH_URL}?{'&'.join(params)}"

    # ---- browser helpers --------------------------------------------------
    def _load_next_data(self, page, url):
        page.goto(url, wait_until="domcontentloaded")
        try:
            page.wait_for_selector("script#__NEXT_DATA__", state="attached", timeout=15000)
        except Exception:
            print(f"[{self.name}] no __NEXT_DATA__ on {url} "
                  f"(title: {page.title()!r}) - blocked or layout changed")
            return None
        raw = page.evaluate("() => document.getElementById('__NEXT_DATA__').textContent")
        return json.loads(raw)

    def _resolve_ids(self, page, make, model, make_id, model_id):
        """Turn names into PistonHeads IDs using the site's own filter lists.

        Returns (make_id, [model_ids]). A model name can map to several ids
        (e.g. "MX-5 Mk1".."Mk4"); each is searched separately and merged.
        """
        model_ids = [str(model_id)] if model_id else []

        if make and not make_id:
            if self._makes is None:
                data = self._load_next_data(page, SEARCH_URL)
                self._makes = facet_options(data, "make") if data else []
            make_id = find_option_id(self._makes, make)
            if not make_id:
                print(f"[{self.name}] could not find make {make!r}; "
                      f"pass make_id explicitly or check the spelling")

        if model and make_id and not model_ids:
            if make_id not in self._models:
                data = self._load_next_data(page, self.build_url(make_id=make_id))
                self._models[make_id] = facet_options(data, "model") if data else []
            options = self._models[make_id]
            model_ids = find_option_ids(options, model)
            if not model_ids:
                labels = ", ".join(label for label, _ in options[:60]) or "(empty)"
                print(f"[{self.name}] no model match for {model!r} under make {make_id}; "
                      f"using make + title matching. Model list: {labels}")
        return make_id, model_ids

    def _click_view_more(self, page):
        """Click "View more"; fall back to a JS click if something blocks the real one."""
        button = page.get_by_role("button", name=re.compile(r"view more", re.I)).first
        for _ in range(6):  # wait for the button to be enabled (it can be disabled while loading)
            try:
                if button.is_enabled():
                    break
            except Exception:
                pass
            page.wait_for_timeout(500)
        try:
            button.scroll_into_view_if_needed(timeout=3000)
            button.click(timeout=3000)
            return True
        except Exception as exc:
            reason = " | ".join(str(exc).strip().splitlines()[:6])[:500]
            try:
                button.evaluate("el => el.click()")  # bypasses overlays / actionability checks
                print(f"[{self.name}] normal click failed, JS click used. Reason: {reason}")
                return True
            except Exception as exc2:
                print(f"[{self.name}] could not click 'View more': {reason} || JS: {str(exc2)[:200]}")
                return False

    def _collect_after_click(self, page, responses, state, known_ids, extra):
        """Wait for the JSON a click triggers; add new organic adverts to `extra`."""
        grew = False
        for _ in range(max(1, int(self.view_more_wait_s * 2))):
            while state["processed"] < len(responses):
                response = responses[state["processed"]]
                state["processed"] += 1
                try:
                    items = extract_adverts_from_json(response.json())
                except Exception:
                    continue
                for item in items:
                    if item["id"] not in known_ids and item["id"] not in extra:
                        extra[item["id"]] = item
                        grew = True
            if grew:
                break
            page.wait_for_timeout(500)
        return grew

    def _fetch_pages(self, page, template, known_ids, extra, target):
        """Fetch the remaining pages directly, moving `offset` like the site does.

        Returns True when finished (complete or end of results), False on a failure
        so the caller can fall back to clicking.
        """
        offset = template["offset"] + template["limit"]
        limit = template["limit"]
        for _ in range(self.max_api_pages):
            if isinstance(target, int) and len(known_ids) + len(extra) >= target:
                return True
            variables = dict(template["variables"], offset=offset, limit=limit)
            query = urlencode({
                "operationName": "SearchPage",
                "variables": json.dumps(variables, separators=(",", ":")),
                "extensions": template["extensions"],
            })
            result = page.evaluate(FETCH_JS, f"{BASE_URL}{API_PATH}?{query}")
            try:
                if result["status"] != 200:
                    raise ValueError(f"HTTP {result['status']}")
                payload = json.loads(result["text"])
            except Exception as exc:
                print(f"[{self.name}] direct API page at offset {offset} failed "
                      f"({str(exc)[:80]}): {str(result.get('text'))[:150]!r}; falling back to clicks")
                return False
            items = extract_adverts_from_json(payload)
            if not items and payload.get("errors"):
                print(f"[{self.name}] direct API returned errors at offset {offset}: "
                      f"{json.dumps(payload['errors'])[:200]}; falling back to clicks")
                return False
            added = 0
            for item in items:
                if item["id"] not in known_ids and item["id"] not in extra:
                    extra[item["id"]] = item
                    added += 1
            if not items or not added:
                return True  # end of results (or only repeats)
            offset += limit
            page.wait_for_timeout(300)  # be polite
        return True

    def _load_more(self, page, known_ids, target):
        """Return the listings beyond the first batch.

        One real click captures the site's own request; the rest are fetched
        directly. Anything that goes wrong falls back to repeated clicking.
        """
        extra, responses, state, api_requests = {}, [], {"processed": 0}, []

        def on_response(response):
            if "json" in (response.headers.get("content-type") or ""):
                responses.append(response)

        def on_request(request):
            if API_PATH in request.url and "operationName=SearchPage" in request.url:
                api_requests.append(request.url)

        def complete():
            return isinstance(target, int) and len(known_ids) + len(extra) >= target

        page.on("response", on_response)
        page.on("request", on_request)
        direct_ok = self.direct_api
        try:
            for _ in range(self.max_clicks):
                if complete():
                    break
                button = page.get_by_role("button", name=re.compile(r"view more", re.I))
                if button.count() == 0 or not button.first.is_visible():
                    break
                if not self._click_view_more(page):
                    break
                if not self._collect_after_click(page, responses, state, known_ids, extra):
                    break  # nothing new arrived: end of results, or the click did nothing

                template = request_template(api_requests[-1]) if api_requests else None
                if not self._announced:
                    self._announced = True
                    if template:
                        print(f"[{self.name}] pagination via {API_PATH} SearchPage "
                              f"(first click: offset={template['offset']} limit={template['limit']})")
                    else:
                        print(f"[{self.name}] no {API_PATH} SearchPage request seen after the click; "
                              f"paginating by clicking only")
                if direct_ok and template and not complete():
                    if self._fetch_pages(page, template, known_ids, extra, target):
                        break
                    direct_ok = False  # keep going by clicking
                page.wait_for_timeout(300)  # let the page re-render before the next click
        finally:
            page.remove_listener("response", on_response)
            page.remove_listener("request", on_request)
        return list(extra.values())

    def _scrape_url(self, page, url):
        """One results URL: first batch from __NEXT_DATA__, the rest via View more."""
        data = self._load_next_data(page, url)
        if data is None:
            return [], None
        listings, meta = extract_listings(data)

        total = meta["total"]
        if isinstance(total, int):
            organic_total = max(total - meta["featured"], 0)  # `total` includes sponsored
            if len(listings) < organic_total:
                listings += self._load_more(page, {x["id"] for x in listings}, organic_total)
            if len(listings) < organic_total:
                print(f"[{self.name}] NOTE: scanned {len(listings)} of {organic_total} organic "
                      f"results for {url}")
        return listings, meta

    # ---- main entry -------------------------------------------------------
    def _perform_scrape(self, page, query):
        make, model, make_id, model_id = self._parse_query(query)
        make_id, model_ids = self._resolve_ids(page, make, model, make_id, model_id)

        urls = [self.build_url(make_id, mid) for mid in model_ids] or [self.build_url(make_id)]
        listings, seen, metas = [], set(), []
        for url in urls:
            items, meta = self._scrape_url(page, url)
            if meta is None:
                continue
            metas.append(meta)
            for item in items:
                if item["id"] not in seen:
                    seen.add(item["id"])
                    listings.append(item)
        if not metas:
            return []

        # Only match on titles for filters the server did not apply.
        tokens = []
        if make and not all(m["make_applied"] for m in metas):
            tokens += make.split()
        if model and not all(m["model_applied"] for m in metas):
            tokens += model.split()
        if (make_id and not all(m["make_applied"] for m in metas)) or \
           (model_ids and not all(m["model_applied"] for m in metas)):
            print(f"[{self.name}] WARNING: server did not apply an ID filter for {query!r} "
                  f"(urls={urls}); relying on title matching")

        scanned = len(listings)
        if tokens:
            listings = [item for item in listings if matches_query(item, tokens)]
        after_title = len(listings)

        # Price window from the query (listings with no usable price are dropped).
        min_price = query.get("min_price") if isinstance(query, dict) else None
        max_price = query.get("max_price") if isinstance(query, dict) else None
        priced_out = 0
        if min_price or max_price:
            kept = []
            for item in listings:
                value = item["price_value"]
                if not value or (max_price and value > max_price) or (min_price and value < min_price):
                    priced_out += 1
                    continue
                kept.append(item)
            listings = kept

        print(f"[{self.name}] {query!r}: searches={len(urls)} "
              f"scanned={scanned} title_matched={after_title} priced_out={priced_out} "
              f"kept={len(listings)} sponsored_skipped={sum(m['featured'] for m in metas)}")
        return listings

    def fetch(self, *args, **kwargs):
        """Return the matching listings (list of dicts). No database, no alerts."""
        page = None
        query = None

        for arg in args:
            if hasattr(arg, "goto"):
                page = arg
            elif isinstance(arg, (dict, str)):
                query = arg

        if not page:
            page = kwargs.get("page")
        if not query:
            query = kwargs.get("query")

        if page and hasattr(page, "goto"):
            return self._perform_scrape(page, query)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(locale="en-GB", timezone_id="Europe/London")
            local_page = context.new_page()
            try:
                return self._perform_scrape(local_page, query)
            finally:
                browser.close()

    # ---- pipeline entry point (main.py: new_count = scraper.scrape(car)) ----------
    @staticmethod
    def _to_db_row(item, query):
        """Map a listing onto the fields utils.db.upsert_listing expects."""
        make, model, _, _ = PistonHeadsScraper._parse_query(query)
        return {
            "url": item["url"],
            "source": SOURCE,
            "external_id": item["id"],
            "make": make or item["make"],      # the query's labels keep sources comparable
            "model": model or item["model"],
            "variant": "",
            "title": item["title"] or item["name"],
            "price_raw": item["price_value"] or 0,
            "price_formatted": item["price"],
            "mileage": item["mileage"],
            "year": item["year"],
            "transmission": item["transmission"],
            "fuel_type": item["fuel"],
            "location": item["location"],
            "seller_type": item["seller_type"],
        }

    def scrape(self, *args, **kwargs):
        """Fetch, skip what the database already has, alert on the rest.

        Returns the number of NEW listings. A listing is only saved once its alert
        has been sent, so a failed Telegram call is retried on the next run.
        """
        from utils.db import is_listing_seen, upsert_listing  # project module, imported lazily

        query = next((a for a in args if isinstance(a, (dict, str))), kwargs.get("query"))
        new_count = sent = unsent = 0

        for item in self.fetch(*args, **kwargs):
            row = self._to_db_row(item, query)
            if is_listing_seen(item["url"].split("?")[0]):
                upsert_listing(row)  # refreshes last_seen and records price changes
                continue

            if sent < self.max_alerts:
                outcome = self.notifier(format_alert(item))
                if outcome is False:
                    continue  # send failed: leave unsaved so the next run retries it
                if outcome:
                    sent += 1
                    time.sleep(self.alert_delay_s)
            else:
                unsent += 1  # over the cap: still saved, summarised below

            if upsert_listing(row):
                new_count += 1

        if unsent:
            make, model, _, _ = self._parse_query(query)
            self.notifier(f"➕ {unsent} more new {make} {model} listings on {SOURCE} were saved "
                          f"but not sent (limit {self.max_alerts} alerts per search).")
        return new_count