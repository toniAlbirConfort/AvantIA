"""Pending email conversations the team already answered from Outlook.

The team often answers from the mailbox instead of from AvantIA, so those conversations stayed
"Pendiente" forever and counted as unanswered. The worker looks up each pending consultation's
customer in the Sent folder: if the team sent an email in the same thread after the customer's
last message, the conversation becomes ``answered`` ("Respondida en el buzón") and the audit
keeps when, from which address and with which subject. A new message from the customer puts it
back to pending (``app.ingest``), as with any answer.

An operator can put it back to pending; the same reply never marks it again, only a newer one.

Automatic notices from platforms (Hotelbeds price updates, Expedia integration reports,
newsletters...) are not consultations either. When every customer message of a pending
conversation comes from a platform or automatic sender and the classifier says "non_demand"
with high confidence (or a person said so), it is filed (``filed``, "Archivada: aviso
automático") so it stops counting as unanswered. Same rules: a new message puts it back to
pending, and an operator can put it back too without the same message filing it again.
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
FILED_ACTION = "conversation.filed_notice"
WINDOW = timedelta(days=30)
# Sent folder look-ups per run. Each run continues after the last conversation it looked up,
# so a long backlog is covered over several runs instead of always the same ones.
MAX_PER_RUN = 150
_cursor = {"after_id": 0}
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


def candidates(db: Session, current: datetime) -> list[tuple[Conversation, str]]:
    """Pending consultations with a customer address, from where the last run stopped.

    Platform notifications and colleagues do not use up the per-run budget.
    """
    rows = db.scalars(
        select(Conversation)
        .where(
            Conversation.status == "pending",
            Conversation.channel == "email",
            Conversation.kind != "booking",
            Conversation.last_inbound_at >= current - WINDOW,
        )
        .order_by(Conversation.id)
    ).all()
    addressed = [(item, address) for item in rows if (address := customer_email(item))]
    start = _cursor["after_id"]
    ordered = [pair for pair in addressed if pair[0].id > start] + [
        pair for pair in addressed if pair[0].id <= start
    ]
    chosen = ordered[:MAX_PER_RUN]
    # Wrap around once the end is reached; next run starts again from the lowest id.
    _cursor["after_id"] = chosen[-1][0].id if len(chosen) < len(ordered) else 0
    return chosen


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
        for conversation, address in conversations:
            after = last_customer_message_at(db, conversation)
            if after is None:
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


def _confident_non_demand(message: Message) -> bool:
    if message.classification_override:
        return message.classification_override == "non_demand"
    return (
        message.classification_label == "non_demand"
        and message.classification_confidence == "high"
    )


def automatic_sender(conversation: Conversation) -> bool:
    """A platform or no-reply sender, never a colleague (relayed leads come from them)."""
    address = parseaddr(conversation.customer_address or "")[1].strip().lower()
    return "@" in address and not _internal(address) and _booking_platform(address)


def file_platform_notices(db: Session, current: datetime | None = None) -> int:
    """File pending automatic notices nobody has to answer. Returns how many."""
    current = current or datetime.now(UTC)
    filed = 0
    for conversation in db.scalars(
        select(Conversation).where(
            Conversation.status == "pending",
            Conversation.channel == "email",
            Conversation.kind != "booking",
            Conversation.last_inbound_at >= current - WINDOW,
        )
    ).all():
        if not automatic_sender(conversation):
            continue
        messages = db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation.id, Message.direction == "inbound")
            .order_by(Message.id)
        ).all()
        if not messages or not all(_confident_non_demand(item) for item in messages):
            continue  # unclassified yet, unsure, or something to act on
        last_id = messages[-1].id
        done = db.scalars(
            select(AuditEvent.detail).where(
                AuditEvent.conversation_id == conversation.id,
                AuditEvent.action == FILED_ACTION,
            )
        ).all()
        if any((detail or {}).get("message_id") == last_id for detail in done):
            continue  # an operator put it back to pending after this same message
        conversation.status = "filed"
        db.add(
            AuditEvent(
                conversation_id=conversation.id,
                action=FILED_ACTION,
                detail={"reason": "aviso_automatico", "message_id": last_id},
            )
        )
        filed += 1
    db.commit()
    return filed


def filed_notice(db: Session, conversation: Conversation) -> dict | None:
    """Why the conversation was filed, while it still is."""
    if conversation.status != "filed":
        return None
    created = db.scalar(
        select(AuditEvent.created_at)
        .where(AuditEvent.conversation_id == conversation.id, AuditEvent.action == FILED_ACTION)
        .order_by(AuditEvent.id.desc())
    )
    return {"filed_at": created, "reason": "aviso_automatico"}


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
