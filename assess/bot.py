"""Telegram bot: send a registration (and advert claims), get the assessment back.

    python -m assess.bot                      # real MOT API (needs MOT_* in .env)
    python -m assess.bot --sample assess/samples/sample_cayman_concerns.json   # any reg -> sample

Long-polls getUpdates, so it runs anywhere with internet access (no webhook or server).
Only answers chats listed in ASSESS_ALLOWED_CHAT_IDS (comma-separated), defaulting to
TELEGRAM_CHAT_ID. Uses the same bot token as the listing alerts.
Never prints exception text: Telegram URLs contain the bot token.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

from dotenv import load_dotenv

from assess.mot import MotClient
from assess.service import HELP, api_lookup, assess_text
from utils.notifier import send_telegram

TELEGRAM_LIMIT = 4000  # Telegram's hard limit is 4096 characters per message
POLL_TIMEOUT_S = 50


def split_message(text, limit=TELEGRAM_LIMIT):
    """Split on line breaks so no chunk exceeds the Telegram limit."""
    chunks, current = [], ""
    for line in text.split("\n"):
        while len(line) > limit:  # a single enormous line: hard-cut it
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


def handle_update(update, allowed_chats, lookup, send):
    """Process one Telegram update. Returns the reply text (or None if ignored)."""
    message = update.get("message") or {}
    chat_id = str((message.get("chat") or {}).get("id", ""))
    text = (message.get("text") or "").strip()
    if not chat_id or not text:
        return None
    if chat_id not in allowed_chats:
        print(f"Ignored a message from a chat that is not allowed ({chat_id}).")
        return None
    reply = HELP if text.split()[0].lower() in ("/start", "/help") else assess_text(text, lookup=lookup)
    for chunk in split_message(reply):
        send(chunk, chat_id)
    return reply


def _telegram(method, token, **params):
    url = f"https://api.telegram.org/bot{token}/{method}?{urlencode(params)}"
    with urllib.request.urlopen(url, timeout=POLL_TIMEOUT_S + 10) as response:
        return json.load(response)


def run(lookup):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    allowed = {c.strip() for c in (os.getenv("ASSESS_ALLOWED_CHAT_IDS") or os.getenv("TELEGRAM_CHAT_ID") or "").split(",") if c.strip()}
    if not token or not allowed:
        sys.exit("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (or ASSESS_ALLOWED_CHAT_IDS) must be set in .env")

    def send(text, chat_id):
        send_telegram(text, chat_id=chat_id, parse_mode=None)

    print(f"Assessment bot running for {len(allowed)} chat(s). Ctrl+C to stop.")
    offset, backoff = None, 5
    while True:
        try:
            params = {"timeout": POLL_TIMEOUT_S, "allowed_updates": json.dumps(["message"])}
            if offset is not None:
                params["offset"] = offset
            updates = _telegram("getUpdates", token, **params).get("result", [])
            backoff = 5
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                print("Telegram says another poller or a webhook is active for this bot (HTTP 409).")
            else:
                print(f"Telegram poll failed: HTTP {exc.code}")
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)
            continue
        except Exception as exc:
            print(f"Telegram poll failed: {type(exc).__name__}")
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)
            continue

        for update in updates:
            offset = update["update_id"] + 1  # acknowledge even if handling fails, so one bad message can't loop
            try:
                handle_update(update, allowed, lookup, send)
            except Exception as exc:
                print(f"Failed to handle a message: {type(exc).__name__}")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m assess.bot")
    parser.add_argument("--sample", help="answer every registration with this saved API response (testing)")
    args = parser.parse_args(argv)
    load_dotenv()
    if args.sample:
        with open(args.sample, encoding="utf-8") as f:
            sample = json.load(f)
        lookup = lambda registration: sample  # noqa: E731
    else:
        if not MotClient.configured():
            sys.exit("MOT API credentials missing (MOT_CLIENT_ID, MOT_CLIENT_SECRET, MOT_API_KEY, MOT_TOKEN_URL); "
                     "use --sample to try the bot with a saved response")
        lookup = api_lookup
    try:
        run(lookup)
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
