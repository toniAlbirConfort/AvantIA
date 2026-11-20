"""Fill the sender's date of emails stored before ``messages.email_date`` existed.

The first load of the inbox (12 November 2026) ingested hundreds of old emails on the same
day, before the Date header was kept. Their ``created_at`` is the load time, so anything that
waits from "when the customer wrote" (the unregistered-consultation deadline) started a week
late. This reads the Date header of each of those emails from the mailbox, by Message-ID, and
moves the received time and deadline of their registration checks to the real date.

Read-only towards the mailbox (readonly=True, BODY.PEEK). Report only by default; pass
--apply to write.

    python -m app.backfill_email_dates            # how many emails and checks would change
    python -m app.backfill_email_dates --apply    # write them
"""

from __future__ import annotations

import argparse
import email
import imaplib
import re
from collections.abc import Iterator
from contextlib import contextmanager
from email.utils import parsedate_to_datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AuditEvent, Message, RegistrationCheck
from app.registration import office_deadline

_SAFE_ID = re.compile(r"^<[^\s\"\\<>]+>$")


@contextmanager
def open_inbox() -> Iterator[imaplib.IMAP4]:
    settings = get_settings()
    if not settings.imap_host or not settings.imap_user:
        raise RuntimeError("IMAP no configurado")
    conn = imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port, timeout=30)
    try:
        conn.login(settings.imap_user, settings.imap_password)
        result, _ = conn.select(settings.imap_mailbox, readonly=True)
        if result != "OK":
            raise RuntimeError("No se pudo abrir el buzón")
        yield conn
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def header_date(conn: imaplib.IMAP4, message_id: str):
    """The Date header of the email with this Message-ID in the selected folder, or None."""
    if not _SAFE_ID.match(message_id):
        return None
    result, data = conn.uid("SEARCH", None, "HEADER", "Message-ID", f'"{message_id}"')
    if result != "OK" or not data or not data[0]:
        return None
    uid = data[0].split()[0]
    result, fetched = conn.uid("FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (DATE)])")
    if result != "OK" or not fetched or not isinstance(fetched[0], tuple):
        return None
    raw = email.message_from_bytes(fetched[0][1]).get("Date")
    try:
        value = parsedate_to_datetime(raw) if raw else None
    except (TypeError, ValueError, IndexError):
        return None
    if value is None or value.tzinfo is None:
        return None
    return value


def backfill(db: Session, conn: imaplib.IMAP4, *, apply: bool = False) -> dict:
    settings = get_settings()
    messages = db.scalars(
        select(Message)
        .where(
            Message.channel == "email",
            Message.direction == "inbound",
            Message.email_date.is_(None),
        )
        .order_by(Message.id)
    ).all()
    found = 0
    checks: list[int] = []
    for message in messages:
        value = header_date(conn, (message.provider_id or "").strip())
        if value is None:
            continue
        found += 1
        check = db.scalar(
            select(RegistrationCheck).where(RegistrationCheck.message_id == message.id)
        )
        if check is not None:
            checks.append(check.id)
        if not apply:
            continue
        message.email_date = value
        if check is not None:
            check.received_at = value
            check.due_at = office_deadline(value, settings.registration_alert_delay_minutes)
    result = {
        "applied": apply,
        "without_date": len(messages),
        "found": found,
        "checks": len(checks),
    }
    if apply and found:
        db.add(AuditEvent(action="messages.email_date_backfilled", detail=result))
        db.commit()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="write the dates")
    args = parser.parse_args()
    from app.db import SessionLocal

    with SessionLocal() as db, open_inbox() as conn:
        print(backfill(db, conn, apply=args.apply))


if __name__ == "__main__":
    main()
