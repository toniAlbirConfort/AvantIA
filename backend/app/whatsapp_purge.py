from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import AuditEvent, BookingTask, Conversation, Draft, Job, Message


def purge_whatsapp_records(db: Session) -> dict[str, int]:
    """Remove all WhatsApp inbox data and work derived from it."""
    conversation_ids = set(
        db.scalars(
            select(Conversation.id).where(Conversation.channel == "whatsapp")
        ).all()
    )
    if not conversation_ids:
        return {
            "conversations": 0,
            "messages": 0,
            "drafts": 0,
            "booking_tasks": 0,
            "audit_events": 0,
            "jobs": 0,
        }

    message_ids = set(
        db.scalars(
            select(Message.id).where(Message.conversation_id.in_(conversation_ids))
        ).all()
    )
    draft_ids = set(
        db.scalars(select(Draft.id).where(Draft.conversation_id.in_(conversation_ids))).all()
    )
    conversation_keys = {str(value) for value in conversation_ids}
    message_keys = {str(value) for value in message_ids}
    draft_keys = {str(value) for value in draft_ids}
    related_jobs = []
    for job in db.scalars(select(Job)).all():
        payload = job.payload if isinstance(job.payload, dict) else {}
        linked_to_data = (
            str(payload.get("conversation_id", "")) in conversation_keys
            or str(payload.get("message_id", "")) in message_keys
            or str(payload.get("draft_id", "")) in draft_keys
        )
        if linked_to_data:
            related_jobs.append(job.id)

    counts = {
        "jobs": db.execute(delete(Job).where(Job.id.in_(related_jobs))).rowcount or 0
        if related_jobs
        else 0,
        "audit_events": db.execute(
            delete(AuditEvent).where(AuditEvent.conversation_id.in_(conversation_ids))
        ).rowcount
        or 0,
        "booking_tasks": db.execute(
            delete(BookingTask).where(BookingTask.conversation_id.in_(conversation_ids))
        ).rowcount
        or 0,
        "drafts": db.execute(
            delete(Draft).where(Draft.conversation_id.in_(conversation_ids))
        ).rowcount
        or 0,
        "messages": db.execute(
            delete(Message).where(Message.conversation_id.in_(conversation_ids))
        ).rowcount
        or 0,
        "conversations": db.execute(
            delete(Conversation).where(Conversation.id.in_(conversation_ids))
        ).rowcount
        or 0,
    }
    return counts
