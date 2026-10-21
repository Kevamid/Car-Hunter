"""Read alert emails from the dedicated mailbox over IMAP (read-only).

Env: ALERTS_EMAIL, ALERTS_APP_PASSWORD (a Google app password, not the account password),
optional ALERTS_IMAP_HOST (default imap.gmail.com). The mailbox is opened READ-ONLY, so
nothing is deleted, moved or even marked as read; which emails were handled is tracked
in the project database by Message-ID instead.
"""
import email
import imaplib
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime

DEFAULT_HOST = "imap.gmail.com"


class MailboxError(Exception):
    pass


@dataclass
class Message:
    message_id: str
    sender: str      # bare address, lower case
    subject: str
    received: datetime | None
    html: str
    text: str


def configured():
    return bool(os.getenv("ALERTS_EMAIL") and os.getenv("ALERTS_APP_PASSWORD"))


def _decode(value):
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:
        return value or ""


def _bodies(msg):
    html = text = ""
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        try:
            payload = part.get_payload(decode=True) or b""
            content = payload.decode(part.get_content_charset() or "utf-8", "replace")
        except Exception:
            continue
        if part.get_content_type() == "text/html":
            html += content
        elif part.get_content_type() == "text/plain":
            text += content
    return html, text


def parse_message(raw):
    msg = email.message_from_bytes(raw)
    html, text = _bodies(msg)
    try:
        received = parsedate_to_datetime(msg.get("Date"))
    except (TypeError, ValueError):
        received = None
    return Message(message_id=(msg.get("Message-ID") or "").strip(),
                   sender=parseaddr(msg.get("From", ""))[1].lower(),
                   subject=_decode(msg.get("Subject")), received=received, html=html, text=text)


def fetch_recent(sender_domains, days=3):
    """Messages from the last `days` days whose From address ends with one of `sender_domains`.
    Never prints credentials; login failures raise MailboxError with a plain explanation."""
    if not configured():
        raise MailboxError("ALERTS_EMAIL / ALERTS_APP_PASSWORD are not set (or are empty)")
    host = os.getenv("ALERTS_IMAP_HOST") or DEFAULT_HOST
    since =(datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
    try:
        imap = imaplib.IMAP4_SSL(host)
    except OSError as exc:
        raise MailboxError(f"could not connect to {host}: {type(exc).__name__}") from None
    try:
        try:
            imap.login(os.getenv("ALERTS_EMAIL"), os.getenv("ALERTS_APP_PASSWORD"))
        except imaplib.IMAP4.error:
            raise MailboxError("login failed: check ALERTS_EMAIL and ALERTS_APP_PASSWORD (a Google app password, "
                               "with 2-step verification on)") from None
        imap.select("INBOX", readonly=True)
        messages = []
        for domain in sender_domains:
            status, data = imap.search(None, "SINCE", since, "FROM", f'"{domain}"')
            if status != "OK":
                continue
            for num in data[0].split():
                status, parts = imap.fetch(num, "(BODY.PEEK[])")  # PEEK: never sets the \Seen flag
                if status == "OK" and parts and isinstance(parts[0], tuple):
                    message = parse_message(parts[0][1])
                    if message.sender.endswith(domain):
                        messages.append(message)
        return messages
    finally:
        try:
            imap.logout()
        except Exception:
            pass
