"""Pending email conversations the team already answered from Outlook.

The team often answers from the mailbox instead of from AvantIA, so those conversations stayed
"Pendiente" forever and counted as unanswered. The worker looks up each pending consultation's
customer in the Sent folder: if the team sent an email in the same thread after the customer's
last message, the conversation becomes ``answered`` ("Respondida en el buzón") and the audit
keeps when, from which address and with which subject. A new message from the customer puts it
back to pending (``app.ingest``), as with any answer.

An operator can put it back to pending; the same reply never marks it again, only a newer one.
Read-only towards the mailbox: the Sent folder is opened with readonly=True and messages are
fetched with BODY.PEEK.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from email.utils import parseaddr

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AuditEvent, Conversation, Message
from app.registration import _booking_platform, _internal
from app.staff_replies import open_sent_folder, sent_to

logger = logging.getLogger(__name__)

ACTION = "conversation.answered_outside"
WINDOW = timedelta(days=30)
MAX_PER_RUN = 200
# Clock differences between the customer's server and the mailbox's.
CLOCK_MARGIN = timedelta(minutes=2)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def customer_email(conversation: Conversation) -> str | None:
    """The address to look up, or None for colleagues, platforms and relayed forms."""
    address = parseaddr(conversation.customer_address or "")[1].strip().lower()
    if "@" not in address or _internal(address) or _booking_platform(address):
        return None
    return address


def candidates(db: Session, current: datetime) -> list[Conversation]:
    return list(
        db.scalars(
            select(Conversation)
            .where(
                Conversation.status == "pending",
                Conversation.channel == "email",
                Conversation.kind != "booking",
                Conversation.last_inbound_at >= current - WINDOW,
            )
            .order_by(Conversation.last_inbound_at.desc())
            .limit(MAX_PER_RUN)
        ).all()
    )


def thread_ids(db: Session, conversation: Conversation) -> set[str]:
    ids = set(
        db.scalars(
            select(Message.provider_id).where(Message.conversation_id == conversation.id)
        ).all()
    )
    ids.add(conversation.external_key)
    return {item.strip() for item in ids if item and item.strip().startswith("<")}


def last_customer_message_at(db: Session, conversation: Conversation) -> datetime | None:
    value = db.scalar(
        select(func.max(func.coalesce(Message.email_date, Message.created_at))).where(
            Message.conversation_id == conversation.id, Message.direction == "inbound"
        )
    )
    return _aware(value) if value else None


def answer_in(found: list[dict], ids: set[str], after: datetime) -> dict | None:
    """Newest email of the list that answers this thread after the customer's last message."""
    for item in found:  # newest first
        if item["sent_at"] < after - CLOCK_MARGIN:
            return None
        if any(thread_id in item["references"] for thread_id in ids):
            return item
    return None


def already_marked(db: Session, conversation: Conversation, reply: dict) -> bool:
    """True if this same reply marked it before (an operator may have put it back to pending)."""
    details = db.scalars(
        select(AuditEvent.detail).where(
            AuditEvent.conversation_id == conversation.id, AuditEvent.action == ACTION
        )
    ).all()
    return any((detail or {}).get("message_id") == reply["message_id"] for detail in details)


def mark_answered(db: Session, conversation: Conversation, reply: dict) -> None:
    conversation.status = "answered"
    db.add(
        AuditEvent(
            conversation_id=conversation.id,
            action=ACTION,
            detail={
                "sent_at": reply["sent_at"].isoformat(),
                "subject": (reply.get("subject") or "")[:500] or None,
                "sender": (reply.get("sender") or "")[:255] or None,
                "message_id": reply["message_id"],
            },
        )
    )


def check_inbox_replies(db: Session, current: datetime | None = None) -> int:
    """Mark as answered the pending conversations with a team reply. Returns how many."""
    settings = get_settings()
    if not settings.imap_host or not settings.imap_user:
        return 0
    current = current or datetime.now(UTC)
    conversations = candidates(db, current)
    if not conversations:
        return 0
    marked = 0
    with open_sent_folder() as conn:
        if conn is None:
            logger.warning("No Sent folder found; pending conversations are not checked")
            return 0
        for conversation in conversations:
            address = customer_email(conversation)
            after = last_customer_message_at(db, conversation)
            if not address or after is None:
                continue
            reply = answer_in(
                sent_to(conn, address, skip_ids=set()), thread_ids(db, conversation), after
            )
            if reply is None or already_marked(db, conversation, reply):
                continue
            mark_answered(db, conversation, reply)
            marked += 1
        db.commit()
    return marked


def outside_reply(db: Session, conversation: Conversation) -> dict | None:
    """The team's reply that marked the conversation as answered, while it still is."""
    if conversation.status != "answered":
        return None
    detail = db.scalar(
        select(AuditEvent.detail)
        .where(AuditEvent.conversation_id == conversation.id, AuditEvent.action == ACTION)
        .order_by(AuditEvent.id.desc())
    )
    if not detail:
        return None
    return {
        "sent_at": detail.get("sent_at"),
        "subject": detail.get("subject"),
        "sender": detail.get("sender"),
    }
