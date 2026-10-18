# Car Hunter

Finds fresh UK car listings for a list of wanted cars and sends them to Telegram. Personal tool for now; ideas for a
curated mailing list / paid sourcing later. Owner: Kev (Windows, PowerShell, local venv on Python 3.14; the GitHub
runner uses Python 3.11).

## Stack and layout
Python, Playwright (sync API), SQLite, GitHub Actions (cron every 2 hours + manual `workflow_dispatch`), Telegram alerts.

- `main.py` loads `config.json` (list of cars) and runs every scraper for every car.
- `scrapers/pistonheads.py` PistonHeads (rewritten; see below).
- `scrapers/car_and_classic.py` Car & Classic.
- `scrapers/base.py` small base class (logger, `is_written_off`) used by Car & Classic.
- `utils/db.py` SQLite helpers. DB file: `data/listings.db` (not in git; the runner gets it from the artifact, and
  if the artifact is missing or expired it starts empty: PistonHeads may then alert on up to 15 listings per search).
- `utils/matching.py` whole-word alias matching shared by the scrapers.
- `utils/notifier.py` shared Telegram sender and HTML alert format used by every scraper.
- `.github/workflows/*.yml` "Car Hunter Pipeline": downloads the previous `listings-db` artifact into `data/`, runs
  `python main.py`, uploads `data/listings.db` again (30 day retention). Secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

## The contract every scraper must follow
`main.py` calls `new_count = scraper.scrape(car)` and logs "Found {new_count} new listings". So `scrape()` must itself
save new listings to the DB, send the alert, and **return an integer count of new listings** (not a list).
`car` is a config dict: `make`, `model`, `max_price`, optional `aliases` (list of title keywords), optionally
`make_id` / `model_id` / `min_price`.

## Database (`utils/db.py`)
- `listings.clean_url` is UNIQUE (URL without query string); this is the de-duplication key.
- `is_listing_seen(clean_url)`; `upsert_listing(row)` returns True only for a NEW row. For existing rows it refreshes
  `last_seen_at` and writes `price_history` when the price changes (price drops are logged but do not alert yet).
- Row keys: url, source, external_id, make, model, variant, title, price_raw (int, NOT NULL), price_formatted, mileage,
  year, transmission, fuel_type, location, seller_type.

## PistonHeads scraper design (`scrapers/pistonheads.py`)
Do not go back to CSS selectors, an ID blacklist or "Sponsored" text checks. They were wrong: the old "fallback IDs"
were real organic results, which is why it returned `[]`.
- Results are read from the page's embedded JSON (`<script id="__NEXT_DATA__">`, Apollo cache). Organic results are in
  `searchPage.adverts`; sponsored ones are in `featuredAdverts` and are ignored.
- URL: `https://www.pistonheads.com/buy/search?make-id=43&model-id=516&created-from-hours=48` (IDs are numeric;
  freshness filter offers 24 / 48 / 168). The old `?k=` keyword parameter does not filter.
- Names are turned into IDs from the site's own make/model facet lists (cached per run). A model name may match
  several ids (e.g. "MX-5 Mk1..Mk4"): each is searched and merged. Matching tiers: exact, contains, all words in any
  order. If nothing matches it falls back to make + title matching and prints the model list.
- `searchPage.total` INCLUDES the up to 3 sponsored adverts. Complete scan = organic + sponsored == total.
- Pagination: the "View more" button does not change the URL. It calls
  `GET /api/graphql?operationName=SearchPage&variables={...offset,limit...}&extensions={persistedQuery...}`.
  We click once, copy that request, then fetch later pages directly with `offset += limit`. Clicking repeatedly is the
  fallback (`direct_api: False` forces it).
- `max_price` / `min_price` from the query are enforced in the scraper. Listings with no usable price (POA, £0) are dropped.
- Alerts: sent BEFORE saving, so a failed Telegram call is retried next run. Cap 15 alerts per search; extras are saved
  and summarised in one message. About 1 message/second. If Telegram env vars are missing, listings are still saved.
- `fetch(query)` returns the listing list (no DB, no alerts); `scrape(query)` is the pipeline entry point.
- The DB `make`/`model` come from the config labels (not PistonHeads' own, e.g. "GT 86"); `variant` is left empty.

## Car & Classic scraper design (`scrapers/car_and_classic.py`)
Same rule as PistonHeads: read embedded JSON, not CSS/card text (the old text-scraping version matched almost anything).
- Results are in `<script data-page="app" type="application/json">` (Inertia): `props.searchResults.data` (~60/page)
  and `.pagination`. `props.searchParams` echoes the filters the site actually applied.
- URL: `/search?make=renault&model=clio&sort=latest&page=2` (slugs). `price_to`, `query` and `country` are IGNORED by
  the site, so price, UK-only (GBP + country GB) and keyword filtering happen in the scraper.
- Model: tries the longest recognised slug ("clio-sport" -> "clio"). Known slugs echo a proper label ("GT86"); unknown
  ones echo themselves. If none is known (e.g. M140i, listed under "1 Series"), it scans the make and matches the title.
- Listings have no listed date: freshness = newest `max_pages` (2) pages + DB de-duplication. The FIRST run of each
  search saves without alerting (`seeded_searches` table) to avoid a burst of old adverts.
- Prices are in pence. Dropped: auctions (price is the current bid; `include_auctions` config), sold, non-UK, POA,
  out of price range, title mismatch, written-off titles (`BaseScraper.is_written_off`).
- Auctions stay skipped (Kev's decision, Oct 2026).

## Aliases (both scrapers, `utils/matching.py`)
Optional `aliases` in a car config are title keywords: a listing must contain at least one as a WHOLE word ("rs"
matches "Clio RS", not "owners"; "200" matches "Clio 200", not "12000 miles"). Checked against the title (PistonHeads:
headline + its model name, so a "Renaultsport Clio" model passes on its own). When the site applied no model filter,
the base model word ("Clio") is also required, so "Megane RS" does not pass for "Clio Sport".

## Verified vs not yet verified
Verified on Kev's machine (live site): model/ID resolution, price filtering, sponsored exclusion, all 5 searches ran
without errors. Verified only against stand-ins (saved page + fake endpoints): direct API pagination, click fallback,
DB/alert flow (run against the real `utils/db.py`). NOT yet verified: any full GitHub Actions run (GitHub's datacenter IPs
may be blocked or served different content by PistonHeads: watch for "no __NEXT_DATA__" in the log), live API
pagination (no search so far exceeded one page), real Telegram delivery from the new code.
Car & Classic (Oct 2026): `fetch_listings` verified live for all 5 configured cars; seeding/alert/retry/cap flow verified
offline with a temp DB and a fake notifier.
First Actions run (17 Oct 2026): PistonHeads works from GitHub. Car & Classic does NOT: Cloudflare serves a managed
challenge (HTTP 403, `cf-mitigated: challenge`, "Just a moment...") to datacenter IPs; the whole site, even robots.txt,
is behind it. The scraper detects this on the first page, logs one warning and skips the site for that run. Do not add
stealth plugins, proxies or challenge solvers to get around it. Plan: a self-hosted runner on Kev's PC (home IP works).

Quick live pagination check (no DB, no alerts; Mazda has ~250 results in 48h):
`python -c "from scrapers.pistonheads import PistonHeadsScraper; print(len(PistonHeadsScraper().fetch({'make': 'Mazda'})))"`
Expect a log line `pagination via /api/graphql SearchPage` and a count around 250.

## Advert assessment (`assess/`, Oct 2026, v1 in progress)
Compares what an advert claims with official DVSA MOT records. Private to Kev until API terms and data protection
are checked. `python -m assess AB12CDE --price 8995 --miles 74000 --colour silver` (or `--text advert.txt`,
`--sample assess/samples/<file>.json` to run offline). Tests: `python -m unittest discover -s tests`.
- `mot.py`: DVSA MOT history API (`GET https://history.mot.api.gov.uk/v1/trade/vehicles/registration/{reg}`),
  Entra client-credentials token (scope `https://tapi.dvsa.gov.uk/.default`) + `X-API-Key`. Env: `MOT_CLIENT_ID`,
  `MOT_CLIENT_SECRET`, `MOT_API_KEY`, `MOT_TOKEN_URL`. Limits 15 req/s, burst 10, 500k/day. Errors never echo secrets.
  Response includes `hasOutstandingRecall` (free recall check). New cars: `motTestDueDate`, no tests.
- `vehicle.py` normalises the response (tests oldest first, km -> miles); `parse.py` extracts plate/price/mileage/
  year/colour/fuel/engine from text; `checks.py` rules -> findings (red/amber/green/info + seller question);
  `report.py` plain-text report (terminal and Telegram); `store.py` caches lookups 24h in `mot_lookups`.
- Samples in `assess/samples/` are SYNTHETIC, built from the official OpenAPI spec; nothing has hit the real API yet.
- DVLA Vehicle Enquiry API registration is closed (Oct 2026): tax status, export flag and V5C-issue date are missing
  until it reopens; add them as optional checks keyed on `DVLA_API_KEY`.
- `service.py`: shared `assess_text()` / `api_lookup()` for the CLI and the bot.
- `bot.py`: Telegram bot (`python -m assess.bot`, or `--sample <file>` to answer any reg with a sample). Long-polls
  `getUpdates` on the same bot as the alerts (@Driver_focused_bot); answers only `ASSESS_ALLOWED_CHAT_IDS`
  (default `TELEGRAM_CHAT_ID`); replies in plain text, split under Telegram's 4096-char limit. Runs on Kev's PC
  for now (GitHub Actions can't host an always-on bot).
- Links: the bot never opens advert pages (automated access); a link-only message gets a site-specific "paste
  the text or send the registration" reply. eBay links are reserved for the eBay API once approved.
- `model_notes.py`: sourced weak spots for Kev's five models, limited by year/engine size; amber when the car's
  own MOT defects match the note's pattern. Review new notes with Kev before adding; cite the source.
- Next: v2 price vs eBay, browser extension, paid write-off/finance checks.

## Status: scrapers PAUSED (Oct 2026)
PistonHeads' terms (updated 23 Feb 2026) forbid scraping/data mining without prior written permission; Car & Classic
likewise has no permission. Both are commented out in `main.py`, and Kev has emailed both sites for permission.
Re-enable a scraper ONLY after its site agrees in writing, and follow any conditions it sets (e.g. an identifying
user agent instead of the Chrome one). Checking `robots.txt` is not enough: always read the site's terms too.
The workflow still runs every 2 hours (free on a public repo), which keeps the `listings-db` artifact from expiring.

## Open items
1. Done: Car & Classic rewritten (see its section above).
2. Done: both scrapers apply `aliases` (see "Aliases" below). "sport" was removed from the Clio aliases because it let
   through non-RS cars ("Clio Campus Sport 1.2").
3. Done (Oct 2026): DB and `.pyc` files untracked, `.gitignore` covers `.env`, `venv/`, `data/`, `__pycache__/`,
   `*.pyc`; old `curl_cffi` debug scripts and the `next_data_debug.json` dump removed; unused `requests` dependency and
   `mark_listing_as_seen` helper removed.
4. Car & Classic on Actions: move the workflow to a self-hosted runner on Kev's PC (see "Verified" above).
   Workflow: add a `concurrency:` group so two runs cannot overlap and overwrite the DB artifact. Consider caching the
   Playwright browser to save Actions minutes.
5. Next source: eBay Motors via the official Browse API (waiting on Kev's developer keys). Other sites checked Oct 2026:
   Gumtree's robots.txt disallows query strings (`Disallow: /*?`, sort/max_price/q/pagination), and plain model pages
   in relevance order miss most new listings, so it is parked. Autotrader (Cloudflare, partner-only API), CarGurus
   (verification wall), eBay web search (Akamai 403), Facebook (login) skipped; Motors.co.uk now redirects to Cazoo.
   No proxy / anti-bot services (e.g. Crawlbase): Kev agreed, legitimate routes only (official APIs, home runner).
6. Ideas: alert on price drops (data already in `price_history`), 24h freshness window, more sources (eBay Browse API is the
   only open API among the sites considered; Autotrader's is partner-only), MOT history check via DVSA API,
   curated newsletter from `is_curated_candidate`.

## Security and conduct
- Never commit `.env` or tokens. If a token ever reaches git history, rotate it with BotFather.
  The repo is PUBLIC. Old commits (up to 2418f33: `.env`, and an early `car_tracker.py`) contain a bot token that
  Kev revoked in Oct 2026; checked 18 Oct 2026 that the current token is not in history.
- Do not print Telegram exception text: urllib errors can embed the URL, which contains the bot token.
- Browser/cURL dumps contain cookies: never commit or share them.
- Scraping may breach the sites' terms; this is personal use at low volume. Check legal position before charging anyone.
- Be gentle: one request at a time, no parallel hammering, keep the 2 hour schedule.

## Tests (optional, not in the repo unless added)
`test_pistonheads.py` (13 offline checks) and `test_integration.py` (7 checks against the real `utils/db.py`) both take a
saved PistonHeads results page as the first argument (`python test_integration.py saved.html utils/db.py`).