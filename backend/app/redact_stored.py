"""Mask payment data in messages that were stored before inbound masking existed.

Report only by default; pass --apply to write. Take a database backup first.

    python -m app.redact_stored            # how many messages and conversations would change
    python -m app.redact_stored --apply    # mask them
"""

from __future__ import annotations

import argparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditEvent, Conversation, Message
from app.sensitive import redact_payment_data

MESSAGE_FIELDS = ("body", "html_body", "subject")


def redact_stored(db: Session, *, apply: bool = False, batch_size: int = 500) -> dict:
    """Mask inbound message text and conversation subjects; return what changed (ids only)."""
    changed_messages: list[int] = []
    changed_conversations: list[int] = []
    last_id = 0
    while True:
        batch = db.scalars(
            select(Message)
            .where(Message.direction == "inbound", Message.id > last_id)
            .order_by(Message.id)
            .limit(batch_size)
        ).all()
        if not batch:
            break
        for message in batch:
            last_id = message.id
            updates = {}
            for field in MESSAGE_FIELDS:
                value = getattr(message, field)
                masked = redact_payment_data(value)
                if masked != value:
                    updates[field] = masked
            if updates:
                changed_messages.append(message.id)
                if apply:
                    for field, masked in updates.items():
                        setattr(message, field, masked)
        if apply:
            db.commit()
    for conversation in db.scalars(select(Conversation).where(Conversation.subject.is_not(None))):
        masked = redact_payment_data(conversation.subject)
        if masked != conversation.subject:
            changed_conversations.append(conversation.id)
            if apply:
                conversation.subject = masked
    result = {
        "applied": apply,
        "messages": len(changed_messages),
        "conversations": len(changed_conversations),
        "message_ids": changed_messages,
        "conversation_ids": changed_conversations,
    }
    if apply and (changed_messages or changed_conversations):
        db.add(
            AuditEvent(
                action="maintenance.payment_data_masked",
                detail={key: value for key, value in result.items() if key != "applied"},
            )
        )
    if apply:
        db.commit()
    return result


def main() -> None:
    from app.db import SessionLocal

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write the masked text")
    args = parser.parse_args()
    with SessionLocal() as db:
        result = redact_stored(db, apply=args.apply)
    verb = "Enmascarados" if result["applied"] else "Se enmascararían"
    print(f"{verb}: {result['messages']} mensajes y {result['conversations']} conversaciones.")
    if result["message_ids"]:
        print("Mensajes:", ", ".join(map(str, result["message_ids"][:200])))
    if not result["applied"] and (result["messages"] or result["conversations"]):
        print("Haz una copia de la base de datos y repite con --apply para aplicarlo.")


if __name__ == "__main__":
    main()
