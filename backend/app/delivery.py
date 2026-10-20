from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parseaddr

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.availability import aware
from app.config import get_settings
from app.integrations import gowa_send, smtp_send
from app.models import AuditEvent, Conversation, Draft, Message


async def send_approved(db: Session, draft_id: int) -> None:
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
            original = db.scalar(
                select(Message)
                .where(Message.conversation_id == conv.id, Message.direction == "inbound")
                .order_by(Message.id.desc())
            )
            if not original:
                raise RuntimeError("Falta el mensaje original")
            address = parseaddr(original.sender or "")[1]
            if not address:
                raise RuntimeError("Destinatario no válido")
            domain = get_settings().smtp_from.rsplit("@", 1)[-1]
            provider_id = f"<avantia-{draft.id}@{domain}>"
            smtp_send(
                to=address,
                subject=original.subject
                if original.subject and original.subject.lower().startswith("re:")
                else f"Re: {original.subject or 'Consulta'}",
                text=draft.text,
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
