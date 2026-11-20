"""Last email the team sent, from the mailbox's Sent folder, to each auto-reply recipient.

The team answers from Outlook, so AvantIA never sees those replies in the inbox. To audit
whether an out-of-hours acknowledgement was followed by a human answer, the worker looks
up each recipient in the Sent folder and stores, on the AutoReply row, the latest email
sent to that address: its date, subject and whether it answers the same thread.

Read-only: the folder is opened with readonly=True and messages are fetched with
BODY.PEEK, so nothing in the mailbox changes. Only addresses that received an auto-reply
are looked up; nothing else in the Sent folder is stored. The body of the team's email is
never stored either: ``staff_message`` fetches it on demand for the audit view.
"""

from __future__ import annotations

import email
import email.header
import email.policy
import imaplib
import logging
import re
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.availability import aware
from app.config import get_settings
from app.html_sanitize import sanitize_html
from app.models import AutoReply, Message

logger = logging.getLogger(__name__)

CHECK_SECONDS = 600
# Recent auto-replies are looked up on every run: a reply may still come. Older ones once.
RECHECK_WINDOW = timedelta(days=30)
MAX_PER_RUN = 200
# Headers of the last few messages to the address are enough to find the newest one.
LAST_UIDS = 10
FIELDS = "(MESSAGE-ID DATE SUBJECT IN-REPLY-TO REFERENCES)"
AUTO_ID = "avantia-auto-"
_SAFE_ADDRESS = re.compile(r"^[^\s\"\\@]+@[^\s\"\\@]+$")


def due(db: Session, current: datetime | None = None) -> list[AutoReply]:
    current = current or datetime.now(UTC)
    return list(
        db.scalars(
            select(AutoReply)
            .where(
                or_(
                    AutoReply.created_at >= current - RECHECK_WINDOW,
                    AutoReply.staff_checked_at.is_(None),
                )
            )
            .order_by(AutoReply.staff_checked_at.is_not(None), AutoReply.id.desc())
            .limit(MAX_PER_RUN)
        ).all()
    )


def _quote(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def sent_folder(conn: imaplib.IMAP4) -> str | None:
    """AUTO_REPLY_SENT_FOLDER, or the folder the server flags as \\Sent."""
    configured = get_settings().auto_reply_sent_folder.strip()
    if configured:
        return configured
    result, boxes = conn.list()
    if result != "OK":
        return None
    for raw in boxes or []:
        line = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
        if "\\Sent" not in line:
            continue
        match = re.search(r'"((?:[^"\\]|\\.)*)"\s*$', line) or re.search(r"(\S+)\s*$", line)
        if match:
            return match.group(1).replace('\\"', '"').replace("\\\\", "\\")
    return None


def latest_sent(conn: imaplib.IMAP4, address: str, *, skip_ids: set[str]) -> dict | None:
    """Newest email in the selected folder addressed to ``address`` (To), or None."""
    if not _SAFE_ADDRESS.match(address):
        return None
    result, data = conn.uid("SEARCH", None, "TO", f'"{address}"')
    if result != "OK" or not data or not data[0]:
        return None
    uids = sorted(data[0].split(), key=int)[-LAST_UIDS:]
    newest: dict | None = None
    for uid in uids:
        result, fetched = conn.uid("FETCH", uid, f"(BODY.PEEK[HEADER.FIELDS {FIELDS}])")
        if result != "OK" or not fetched or not isinstance(fetched[0], tuple):
            continue
        headers = email.message_from_bytes(fetched[0][1])
        message_id = (headers.get("Message-ID") or "").strip()
        if message_id in skip_ids or AUTO_ID in message_id:
            continue  # AvantIA's own acknowledgements, not the team's.
        try:
            sent_at = parsedate_to_datetime(headers.get("Date"))
        except (TypeError, ValueError):
            continue
        if sent_at is None or sent_at.tzinfo is None:
            continue
        if newest is None or sent_at > newest["sent_at"]:
            newest = {
                "uid": uid,
                "sent_at": sent_at.astimezone(UTC),
                "subject": _header_text(headers.get("Subject")),
                "references": " ".join(
                    headers.get(name) or "" for name in ("In-Reply-To", "References")
                ),
            }
    return newest


def _header_text(value: str | None) -> str | None:
    if not value:
        return None
    try:
        text = str(email.header.make_header(email.header.decode_header(value)))
    except Exception:
        text = value
    return " ".join(text.split())[:500] or None


def apply(reply: AutoReply, customer_message_id: str | None, found: dict | None,
          checked_at: datetime) -> None:
    reply.staff_checked_at = checked_at
    if not found:
        reply.staff_last_sent_at = None
        reply.staff_last_subject = None
        reply.staff_last_in_thread = None
        return
    ids = [item for item in (customer_message_id, reply.provider_id) if item]
    reply.staff_last_sent_at = found["sent_at"]
    reply.staff_last_subject = found["subject"]
    reply.staff_last_in_thread = any(item in found["references"] for item in ids)


@contextmanager
def open_sent_folder():
    """IMAP connection with the Sent folder selected read-only; yields None without one."""
    settings = get_settings()
    if not settings.imap_host or not settings.imap_user:
        raise RuntimeError("IMAP no configurado")
    conn = imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port, timeout=30)
    try:
        conn.login(settings.imap_user, settings.imap_password)
        folder = sent_folder(conn)
        if not folder:
            yield None
            return
        result, _ = conn.select(_quote(folder), readonly=True)
        if result != "OK":
            raise RuntimeError("No se pudo abrir la carpeta de enviados")
        yield conn
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def check_staff_replies(db: Session) -> int:
    """Refresh the last staff email for the auto-replies due. Returns how many were checked."""
    settings = get_settings()
    if not settings.imap_host or not settings.imap_user:
        return 0
    replies = due(db)
    if not replies:
        return 0
    with open_sent_folder() as conn:
        if conn is None:
            logger.warning("No Sent folder found; staff replies are not checked")
            return 0
        own_ids = {reply.provider_id for reply in replies if reply.provider_id}
        checked_at = datetime.now(UTC)
        for reply in replies:
            message = db.get(Message, reply.message_id)
            found = latest_sent(conn, reply.recipient.strip().lower(), skip_ids=own_ids)
            apply(reply, message.provider_id if message else None, found, checked_at)
        db.commit()
        return len(replies)


def fetch_message(conn: imaplib.IMAP4, uid: bytes | str) -> dict | None:
    """Full email by UID (BODY.PEEK: it stays unread), with its HTML sanitized."""
    from app.mail import _email_content

    result, fetched = conn.uid("FETCH", uid, "(BODY.PEEK[])")
    if result != "OK" or not fetched or not isinstance(fetched[0], tuple):
        return None
    parsed = email.message_from_bytes(fetched[0][1], policy=email.policy.default)
    body, html_body = _email_content(parsed)
    try:
        sent_at = parsedate_to_datetime(parsed.get("Date"))
    except (TypeError, ValueError):
        sent_at = None
    return {
        "sender": _header_text(parsed.get("From")),
        "recipient": _header_text(parsed.get("To")),
        "cc": _header_text(parsed.get("Cc")),
        "subject": _header_text(parsed.get("Subject")),
        "sent_at": sent_at,
        "body": body,
        "html_body": sanitize_html(html_body) if html_body else None,
    }


def staff_message(reply: AutoReply) -> dict | None:
    """The team's last email to the recipient, read now from the Sent folder."""
    with open_sent_folder() as conn:
        if conn is None:
            return None
        skip = {reply.provider_id} if reply.provider_id else set()
        found = latest_sent(conn, reply.recipient.strip().lower(), skip_ids=skip)
        return fetch_message(conn, found["uid"]) if found else None


def staff_reply_view(reply: AutoReply) -> dict | None:
    """What the audit view shows; None until the Sent folder has been checked."""
    if reply.staff_checked_at is None:
        return None
    last = aware(reply.staff_last_sent_at) if reply.staff_last_sent_at else None
    created = aware(reply.created_at)
    return {
        "checked_at": reply.staff_checked_at,
        "last_sent_at": last,
        "subject": reply.staff_last_subject,
        "in_thread": reply.staff_last_in_thread,
        "after_auto_reply": bool(last and last > created),
        "hours_after": round((last - created).total_seconds() / 3600, 1)
        if last and last > created
        else None,
    }
