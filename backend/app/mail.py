from __future__ import annotations

import email
import imaplib
import logging
import re
from datetime import UTC, datetime
from email import policy
from email.utils import parseaddr
from html.parser import HTMLParser
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.ingest import email_key, fallback_message_id, ingest
from app.models import MailCursor

logger = logging.getLogger(__name__)


class _TextFromHtml(HTMLParser):
    block_tags = {"br", "div", "p", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "head"}:
            self.hidden_depth += 1
            return
        if self.hidden_depth:
            return
        if tag in self.block_tags:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "head"}:
            self.hidden_depth = max(0, self.hidden_depth - 1)
            return
        if self.hidden_depth:
            return
        if tag in self.block_tags:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)


def _email_content(message: email.message.EmailMessage) -> tuple[str, str | None]:
    plain_part = message.get_body(preferencelist=("plain",))
    html_part = message.get_body(preferencelist=("html",))
    html_body = None
    if html_part is not None:
        try:
            html_body = str(html_part.get_content())[:100_000]
            html_body = re.sub(
                r"(?is)<meta\b(?=[^>]*\bhttp-equiv\s*=\s*(?:\"refresh\"|'refresh'|refresh(?:\s|/?>)))[^>]*>",
                "",
                html_body,
            )
        except (LookupError, ValueError, TypeError):
            html_body = None
    if plain_part is not None:
        content = str(plain_part.get_content())
    elif html_body:
        parser = _TextFromHtml()
        parser.feed(html_body)
        content = "".join(parser.parts)
    else:
        content = ""
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    content = re.sub(r"[ \t]+\n", "\n", content)
    content = re.sub(r"\n{3,}", "\n\n", content).strip()
    return content[:20_000], html_body


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


def _start_uid(conn: imaplib.IMAP4, *, backfill: int, next_uid: int) -> int:
    if backfill <= 0:
        return max(next_uid - 1, 0)
    result, data = conn.uid("SEARCH", None, "ALL")
    if result != "OK":
        raise RuntimeError("No se pudo enumerar el buzón para la carga inicial")
    uids = [int(item) for item in (data[0] or b"").split()]
    if len(uids) <= backfill:
        return 0
    return uids[-backfill - 1]


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
            start_uid = _start_uid(
                conn, backfill=settings.imap_initial_backfill, next_uid=next_uid
            )
            cursor = MailCursor(
                mailbox=settings.imap_mailbox,
                uidvalidity=validity,
                last_uid=start_uid,
                last_success_at=datetime.now(UTC),
            )
            db.add(cursor)
            db.commit()
        elif cursor.uidvalidity != validity:
            cursor.uidvalidity = validity
            cursor.last_uid = _start_uid(
                conn, backfill=settings.imap_initial_backfill, next_uid=next_uid
            )
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
                body, html_body = _email_content(message)
                _, duplicate = ingest(
                    db,
                    channel="email",
                    external_key=key,
                    provider_id=provider_id,
                    body=body,
                    html_body=html_body,
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
            conn.logout()
        except Exception:
            pass
    return count
