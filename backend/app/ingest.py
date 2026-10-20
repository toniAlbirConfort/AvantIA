from __future__ import annotations

import hashlib
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditEvent, Conversation, Job, Message, now


def enqueue(db: Session, *, key: str, kind: str, payload: dict) -> None:
    if db.scalar(select(Job.id).where(Job.key == key)) is None:
        db.add(Job(key=key, kind=kind, payload=payload))


def email_key(db: Session, *, message_id: str, references: str | None) -> str:
    for ref in re.findall(r"<[^>]+>", references or ""):
        prior = db.scalar(
            select(Message).where(Message.channel == "email", Message.provider_id == ref)
        )
        if prior:
            conv = db.get(Conversation, prior.conversation_id)
            if conv:
                return conv.external_key
    return message_id


def ingest(
    db: Session,
    *,
    channel: str,
    external_key: str,
    provider_id: str,
    body: str,
    sender: str,
    recipient: str | None = None,
    customer_name: str | None = None,
    subject: str | None = None,
    references: str | None = None,
    source_url: str | None = None,
    booking: bool = False,
) -> tuple[Message, bool]:
    duplicate = db.scalar(
        select(Message).where(Message.channel == channel, Message.provider_id == provider_id)
    )
    if duplicate:
        return duplicate, True
    conv = db.scalar(
        select(Conversation).where(
            Conversation.channel == channel, Conversation.external_key == external_key
        )
    )
    if conv is None:
        conv = Conversation(
            channel=channel,
            external_key=external_key,
            customer_name=customer_name,
            customer_address=sender,
            subject=subject,
            kind="booking" if booking else "inquiry",
            priority="high" if booking else "normal",
        )
        db.add(conv)
        db.flush()
    else:
        conv.customer_name = customer_name or conv.customer_name
        conv.subject = subject or conv.subject
        conv.customer_address = sender or conv.customer_address
        conv.last_inbound_at = now()
        conv.overdue_alerted_at = None
        conv.status = "pending"
        if booking:
            conv.kind = "booking"
            conv.priority = "high"
    message = Message(
        conversation_id=conv.id,
        channel=channel,
        provider_id=provider_id,
        direction="inbound",
        body=body,
        subject=subject,
        sender=sender,
        recipient=recipient,
        references=references,
        source_url=source_url,
    )
    db.add(message)
    db.flush()
    enqueue(db, key=f"analyze:{message.id}", kind="analyze", payload={"message_id": message.id})
    enqueue(
        db,
        key=f"alert:new:{message.id}",
        kind="alert",
        payload={"conversation_id": conv.id, "reason": "new"},
    )
    db.add(
        AuditEvent(conversation_id=conv.id, action="message.received", detail={"channel": channel})
    )
    return message, False


def fallback_message_id(raw: bytes) -> str:
    return f"<avantia-{hashlib.sha256(raw).hexdigest()}@local>"
