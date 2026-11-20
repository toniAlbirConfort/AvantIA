"""Audit log of the messages AvantIA sends to customers without a human approving them.

Every automatic sender is a *source* here, normalised to one row shape (channel, recipient,
subject, text, status...), so the Comunicación > Envíos automáticos view can monitor them
together. Today the only automatic sender is the out-of-hours acknowledgement (email,
``app.auto_reply``). A new automatic sender — a WhatsApp notice, for instance — must add its
own source to ``SOURCES`` so it shows up in the log; drafts approved by an operator are not
automatic and stay out.

status is one of: sent, shadow (decided but deliberately not sent), failed, and
delivery_unknown (the send started but never finished: it is never retried automatically).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import AuditEvent, AutoReply, Conversation
from app.staff_replies import staff_reply_view

STATUSES = ("sent", "shadow", "failed", "delivery_unknown")
# Whether the team wrote to the recipient (Sent folder) after the automatic message.
STAFF_REPLY_FILTERS = ("after", "none")
CHANNELS = ("email", "whatsapp")


@dataclass(frozen=True)
class Source:
    kind: str
    label: str
    channel: str


AUTO_REPLY = Source("auto_reply", "Autorespuesta fuera de horario", "email")
SOURCES = (AUTO_REPLY,)

# AutoReply.mode -> status shown in the log.
_AUTO_REPLY_STATUS = {
    "sent": "sent",
    "shadow": "shadow",
    "failed": "failed",
    "sending": "delivery_unknown",
}
_STATUS_MODES = {status: mode for mode, status in _AUTO_REPLY_STATUS.items()}


def _auto_reply_query(
    *,
    status: str | None,
    q: str | None,
    start: datetime | None,
    end: datetime | None,
    staff_reply: str | None = None,
):
    query = select(AutoReply, Conversation).join(
        Conversation, Conversation.id == AutoReply.conversation_id
    )
    if status:
        query = query.where(AutoReply.mode == _STATUS_MODES[status])
    if start:
        query = query.where(AutoReply.created_at >= start)
    if end:
        query = query.where(AutoReply.created_at < end)
    if staff_reply == "after":
        query = query.where(AutoReply.staff_last_sent_at > AutoReply.created_at)
    elif staff_reply == "none":
        # Checked, and nothing sent to the address after the automatic message.
        query = query.where(
            AutoReply.staff_checked_at.is_not(None),
            or_(
                AutoReply.staff_last_sent_at.is_(None),
                AutoReply.staff_last_sent_at <= AutoReply.created_at,
            ),
        )
    if q and q.strip():
        needle = f"%{q.strip().lower()}%"
        query = query.where(
            or_(
                func.lower(AutoReply.recipient).like(needle),
                func.lower(AutoReply.subject).like(needle),
                func.lower(func.coalesce(Conversation.customer_name, "")).like(needle),
            )
        )
    return query


def list_automatic_messages(
    db: Session,
    *,
    channel: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    q: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    staff_reply: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Newest first. ``counts`` covers the same filters except status, for the summary."""
    sources = [
        source
        for source in SOURCES
        if (not channel or source.channel == channel) and (not kind or source.kind == kind)
    ]
    counts = {item: 0 for item in STATUSES}
    items: list[dict] = []
    total = 0
    if AUTO_REPLY in sources:
        base = _auto_reply_query(
            status=None, q=q, start=start, end=end, staff_reply=staff_reply
        ).subquery()
        for mode, amount in db.execute(
            select(base.c.mode, func.count()).group_by(base.c.mode)
        ).all():
            counts[_AUTO_REPLY_STATUS.get(mode, "delivery_unknown")] += amount
        query = _auto_reply_query(
            status=status, q=q, start=start, end=end, staff_reply=staff_reply
        )
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = db.execute(
            query.order_by(AutoReply.created_at.desc(), AutoReply.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        copy_failed = _copy_failures(db, [reply.message_id for reply, _ in rows])
        items = [
            _auto_reply_row(reply, conv, reply.message_id in copy_failed)
            for reply, conv in rows
        ]
    return {
        "items": items,
        "total": total,
        "counts": counts,
        "sources": [
            {"kind": source.kind, "label": source.label, "channel": source.channel}
            for source in SOURCES
        ],
    }


def _copy_failures(db: Session, message_ids: list[int]) -> set[int]:
    """Messages whose copy to the mailbox's sent folder failed (the send itself worked)."""
    if not message_ids:
        return set()
    wanted = set(message_ids)
    found: set[int] = set()
    for detail in db.scalars(
        select(AuditEvent.detail).where(AuditEvent.action == "auto_reply.copy_failed")
    ).all():
        message_id = (detail or {}).get("message_id")
        if message_id in wanted:
            found.add(message_id)
    return found


def _auto_reply_row(reply: AutoReply, conv: Conversation, copy_failed: bool) -> dict:
    return {
        "id": f"{AUTO_REPLY.kind}:{reply.id}",
        "kind": AUTO_REPLY.kind,
        "kind_label": AUTO_REPLY.label,
        "channel": AUTO_REPLY.channel,
        "status": _AUTO_REPLY_STATUS.get(reply.mode, "delivery_unknown"),
        "recipient": reply.recipient,
        "customer_name": conv.customer_name,
        "subject": reply.subject,
        "text": reply.text,
        "language": reply.language,
        "error": reply.error,
        "copy_failed": copy_failed,
        "conversation_id": reply.conversation_id,
        "conversation_kind": conv.kind,
        "staff_reply": staff_reply_view(reply),
        "created_at": reply.created_at,
    }
