from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from email.utils import format_datetime, getaddresses, parseaddr

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.availability import aware
from app.config import READ_ONLY_MODE, get_settings
from app.html_sanitize import text_to_html
from app.integrations import gowa_send, smtp_send
from app.models import AuditEvent, Conversation, Draft, Message


def reply_subject(subject: str | None) -> str:
    subject = (subject or "").strip()
    return subject if subject.lower().startswith("re:") else f"Re: {subject or 'Consulta'}"


def reply_original(db: Session, conv: Conversation) -> Message | None:
    return db.scalar(
        select(Message)
        .where(Message.conversation_id == conv.id, Message.direction == "inbound")
        .order_by(Message.id.desc())
    )


def reply_defaults(db: Session, conv: Conversation) -> dict:
    """Who an email reply goes to and how it is titled, as the composer should show it."""
    original = reply_original(db, conv)
    return {
        "mailbox": get_settings().smtp_from or None,
        "to": parseaddr(original.sender or "")[1] if original else None,
        "subject": reply_subject(original.subject if original else conv.subject),
    }


VALID_ADDRESS = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+$")


def parse_cc(value: str | None) -> list[str]:
    """Addresses from a comma- or semicolon-separated list; ValueError if any is invalid."""
    addresses = []
    for _, address in getaddresses([(value or "").replace(";", ",")]):
        if not address:
            continue
        if not VALID_ADDRESS.match(address):
            raise ValueError(address)
        if address.lower() not in addresses:
            addresses.append(address.lower())
    return addresses


def _quoted(original: Message, text: str, html_body: str) -> tuple[str, str]:
    when = format_datetime(aware(original.created_at)) if original.created_at else ""
    header = f"El {when}, {original.sender or 'el remitente'} escribió:"
    quoted_text = "\n".join("> " + line for line in (original.body or "").splitlines())
    text = f"{text}\n\n{header}\n{quoted_text}"
    html_body = (
        f"{html_body}<br><div>{html.escape(header)}</div>"
        '<blockquote style="margin: 0 0 0 0.8ex; border-left: 1px solid #ccc; '
        'padding-left: 1ex">'
        f"{text_to_html(original.body or '')}</blockquote>"
    )
    return text, html_body


async def send_approved(db: Session, draft_id: int) -> None:
    if READ_ONLY_MODE:
        raise RuntimeError("Modo de solo lectura: los envíos están desactivados")
    draft = db.get(Draft, draft_id)
    if not draft or draft.status != "queued":
        return
    conv = db.get(Conversation, draft.conversation_id)
    if not conv or conv.kind == "booking":
        return
    if draft.approved_at and aware(conv.last_inbound_at) > aware(draft.approved_at):
        draft.status = "superseded"
        conv.status = "pending"
        db.add(
            AuditEvent(
                conversation_id=conv.id,
                action="draft.superseded",
                detail={"reason": "new inbound after approval"},
            )
        )
        db.commit()
        return
    draft.status = "sending"
    conv.status = "sending"
    db.commit()  # From here, a crash is an unknown delivery, never an automatic resend.
    provider_id = None
    try:
        if conv.channel == "whatsapp":
            provider_id = await gowa_send(conv.external_key, draft.text)
        else:
            original = reply_original(db, conv)
            if not original:
                raise RuntimeError("Falta el mensaje original")
            address = parseaddr(original.sender or "")[1]
            if not address:
                raise RuntimeError("Destinatario no válido")
            domain = parseaddr(get_settings().smtp_from)[1].rsplit("@", 1)[-1]
            provider_id = f"<avantia-{draft.id}@{domain}>"
            subject = (draft.subject or "").strip() or reply_subject(original.subject)
            body_html = draft.html_body or text_to_html(draft.text)
            text, quoted_html = _quoted(original, draft.text, body_html)
            smtp_send(
                to=address,
                cc=parse_cc(draft.cc),
                subject=subject,
                text=text,
                html_body=quoted_html,
                message_id=provider_id,
                in_reply_to=original.provider_id,
                references=((original.references or "") + " " + original.provider_id).strip(),
            )
        db.add(
            Message(
                conversation_id=conv.id,
                channel=conv.channel,
                provider_id=provider_id or f"avantia-out-{draft.id}",
                direction="outbound",
                body=draft.text,
                html_body=draft.html_body if conv.channel == "email" else None,
                subject=draft.subject if conv.channel == "email" else None,
                sender=get_settings().smtp_from if conv.channel == "email" else None,
                recipient=conv.customer_address,
                reply_to_id=None,
            )
        )
        draft.status = "sent"
        draft.provider_id = provider_id
        draft.sent_at = datetime.now(UTC)
        conv.status = "sent"
        db.add(
            AuditEvent(
                actor_user_id=draft.approved_by_id,
                conversation_id=conv.id,
                action="draft.sent",
                detail={"channel": conv.channel},
            )
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        draft = db.get(Draft, draft_id)
        conv = db.get(Conversation, draft.conversation_id)
        draft.status = "delivery_unknown"
        draft.error = f"{type(exc).__name__}: {str(exc)[:300]}"
        conv.status = "delivery_unknown"
        db.add(
            AuditEvent(
                conversation_id=conv.id,
                action="draft.delivery_unknown",
                detail={"error_type": type(exc).__name__},
            )
        )
        db.commit()
