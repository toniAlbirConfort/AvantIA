from __future__ import annotations

import email
import imaplib
import logging
import re
from datetime import UTC, datetime
from email import policy
from email.utils import parseaddr
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.ingest import email_key, fallback_message_id, ingest
from app.models import MailCursor

logger = logging.getLogger(__name__)


def _body(message: email.message.EmailMessage) -> str:
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    content = part.get_content()
    if part.get_content_type() == "text/html":
        content = re.sub(r"<[^>]+>", " ", content)
    return re.sub(r"\s+", " ", content).strip()[:20000]


def _booking(sender: str) -> bool:
    address = parseaddr(sender)[1].lower()
    domain = address.rsplit("@", 1)[-1]
    domains = [item.strip().lower() for item in get_settings().booking_sender_domains.split(",")]
    return any(
        domain == allowed or domain.endswith("." + allowed) for allowed in domains if allowed
    )


def _source_link(message_id: str) -> str | None:
    template = get_settings().mail_message_url_template
    return template.replace("{message_id}", quote(message_id, safe="")) if template else None


def poll_mail(db: Session) -> int:
    settings = get_settings()
    if not settings.imap_host:
        return 0
    conn = imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port, timeout=30)
    count = 0
    try:
        conn.login(settings.imap_user, settings.imap_password)
        result, _ = conn.select(settings.imap_mailbox, readonly=True)
        if result != "OK":
            raise RuntimeError("No se pudo abrir el buzón")
        result, status = conn.status(settings.imap_mailbox, "(UIDVALIDITY UIDNEXT)")
        if result != "OK":
            raise RuntimeError("No se pudo leer el estado IMAP")
        status_text = status[0].decode(errors="replace")
        validity = int(re.search(r"UIDVALIDITY\s+(\d+)", status_text).group(1))
        next_uid = int(re.search(r"UIDNEXT\s+(\d+)", status_text).group(1))
        cursor = db.scalar(select(MailCursor).where(MailCursor.mailbox == settings.imap_mailbox))
        if cursor is None:
            start_uid = max(next_uid - settings.imap_initial_backfill - 1, 0)
            cursor = MailCursor(
                mailbox=settings.imap_mailbox, uidvalidity=validity, last_uid=start_uid
            )
            db.add(cursor)
            db.commit()
            return 0
        if cursor.uidvalidity != validity:
            cursor.uidvalidity = validity
            cursor.last_uid = max(next_uid - max(settings.imap_initial_backfill, 200) - 1, 0)
            db.commit()
        result, data = conn.uid("SEARCH", None, "UID", f"{cursor.last_uid + 1}:*")
        if result != "OK":
            raise RuntimeError("Falló la búsqueda IMAP")
        uids = [int(item) for item in (data[0] or b"").split() if int(item) > cursor.last_uid]
        for uid in sorted(uids)[:50]:
            try:
                result, fetched = conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
                if result != "OK":
                    raise RuntimeError(f"Falló la lectura IMAP UID {uid}")
                raw = b"".join(item[1] for item in fetched if isinstance(item, tuple))
                if not raw:
                    raise RuntimeError(f"Mensaje vacío UID {uid}")
                message = email.message_from_bytes(raw, policy=policy.default)
                if message.get("Auto-Submitted", "no").lower() != "no" or message.get(
                    "Precedence", ""
                ).lower() in {"bulk", "list", "junk"}:
                    cursor.last_uid = uid
                    db.commit()
                    continue
                provider_id = str(message.get("Message-ID") or fallback_message_id(raw)).strip()
                sender = str(message.get("From", ""))[:255]
                if not parseaddr(sender)[1]:
                    cursor.last_uid = uid
                    db.commit()
                    continue
                references = " ".join(
                    filter(
                        None,
                        [str(message.get("References", "")), str(message.get("In-Reply-To", ""))],
                    )
                )
                key = email_key(db, message_id=provider_id, references=references)
                _, duplicate = ingest(
                    db,
                    channel="email",
                    external_key=key,
                    provider_id=provider_id,
                    body=_body(message),
                    sender=sender,
                    recipient=str(message.get("To", ""))[:255],
                    customer_name=parseaddr(sender)[0] or None,
                    subject=str(message.get("Subject", ""))[:500],
                    references=references,
                    source_url=_source_link(provider_id),
                    booking=_booking(sender),
                )
                count += int(not duplicate)
                cursor.last_uid = uid
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("IMAP UID %s failed; cursor remains unchanged", uid)
                break
        cursor.last_success_at = datetime.now(UTC)
        cursor.last_error = None
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("IMAP poll failed")
        cursor = db.scalar(select(MailCursor).where(MailCursor.mailbox == settings.imap_mailbox))
        if cursor:
            cursor.last_error = type(exc).__name__
            db.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass
        try:
            conn.logout()
        except Exception:
            pass
    return count
