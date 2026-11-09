"""Run the assessments queued from the dashboard ("Assess this car").

The page adds a row to Supabase `assessments` (supabase/005_assessments.sql); each pipeline run picks up
queued rows, runs the same assessment as the Telegram bot (assess.service.assess_text: MOT history,
model weak spots, repair costs, price against market), writes the report back for the dashboard and
sends it to Telegram. The DVSA keys only live in GitHub secrets / .env, never in the page.

    python -m assess.queue
"""
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone

MAX_PER_RUN = 10


def _rest(method, path, body=None):
    base, key = os.getenv("SUPABASE_URL", "").rstrip("/"), os.getenv("SUPABASE_SERVICE_KEY", "")
    request = urllib.request.Request(f"{base}/rest/v1/{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"apikey": key, "Content-Type": "application/json", "Prefer": "return=minimal"})
    with urllib.request.urlopen(request, timeout=30) as response:
        text = response.read().decode()
        return json.loads(text) if text else None


def queued(limit=MAX_PER_RUN):
    return _rest("GET", f"assessments?status=eq.queued&order=requested_at&limit={limit}")


def save(row_id, status, report):
    _rest("PATCH", f"assessments?id=eq.{int(row_id)}",
          {"status": status, "report": report, "done_at": datetime.now(timezone.utc).isoformat()})


def request_text(row):
    """The request as the bot would receive it: "AB12CDE £8,995 74000 miles 2009" (+ the eBay link)."""
    parts = [row.get("registration") or ""]
    if row.get("price"):
        parts.append(f"£{int(row['price']):,}")
    if row.get("mileage"):
        parts.append(f"{int(row['mileage'])} miles")
    if row.get("year"):
        parts.append(str(row["year"]))
    if "ebay." in (row.get("url") or ""):
        parts.append(row["url"])  # eBay: the listing's own details through the official API
    return " ".join(p for p in parts if p)


def run(rows, assess=None, notify=None, store=save):
    """Assess each queued row, save its report, then send it to Telegram. Returns the number handled."""
    if assess is None:
        from assess.service import assess_text
        assess = assess_text
    if notify is None:
        from utils.notifier import send_telegram
        notify = lambda text: send_telegram(text, parse_mode=None)  # noqa: E731 (reports are plain text)
    from assess.bot import split_message
    handled = 0
    for row in rows:
        text = request_text(row)
        try:
            report, status = assess(text), "done"
        except Exception as exc:  # one bad request must not stop the others
            report, status = f"The assessment failed ({type(exc).__name__}). Try again later.", "error"
        try:
            store(row["id"], status, report)
        except Exception as exc:
            print(f"Assessment {row['id']}: couldn't save the report ({type(exc).__name__}); retried next run")
            continue
        handled += 1
        heading = f"🔎 Assessment: {row.get('title') or row.get('registration') or 'car'}"
        for chunk in split_message(f"{heading}\n\n{report}"):
            notify(chunk)
    return handled


def main():
    from dotenv import load_dotenv
    load_dotenv()
    from utils.supabase_sync import configured
    if not configured():
        print("Assessments skipped: SUPABASE_URL / SUPABASE_SERVICE_KEY not set.")
        return
    try:
        rows = queued()
    except Exception as exc:
        print(f"Assessments skipped: couldn't read the queue ({type(exc).__name__}; run supabase/005_assessments.sql?)")
        return
    if not rows:
        print("Assessments: none queued.")
        return
    from assess.mot import MotClient
    from assess.service import assess_text
    assess = assess_text
    if not MotClient.configured():
        print("Assessments: DVSA MOT keys not set.")

        def assess(text):
            return ("The MOT check isn't set up on the pipeline yet: add the DVSA keys (MOT_CLIENT_ID, "
                    "MOT_CLIENT_SECRET, MOT_API_KEY, MOT_TOKEN_URL) as GitHub secrets.")
    from utils.db import init_db
    init_db()  # MOT lookups are cached in the project database
    print(f"Assessments: {run(rows, assess=assess)} of {len(rows)} done.")


if __name__ == "__main__":
    main()
