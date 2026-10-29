from __future__ import annotations

from sqlalchemy import and_, or_

from app.models import Conversation, Message, now

CLASSIFICATION_LABELS = {"demand", "guest_service", "non_demand", "uncertain"}
CORRECTABLE_CLASSIFICATIONS = {"demand", "guest_service", "non_demand"}
CLASSIFICATION_CONFIDENCE = {"high", "medium", "low"}


def effective_classification(message: Message) -> str | None:
    return message.classification_override or message.classification_label


def needs_classification_review(message: Message) -> bool:
    if not message.classification_label or message.classification_reviewed_at:
        return False
    return (
        message.classification_label == "uncertain"
        or message.classification_confidence != "high"
    )


def classification_review_clause():
    return and_(
        Message.direction == "inbound",
        Message.classification_label.is_not(None),
        Message.classification_reviewed_at.is_(None),
        or_(
            Message.classification_label == "uncertain",
            Message.classification_confidence.is_(None),
            Message.classification_confidence != "high",
        ),
    )


def _queue_label(message: Message) -> str:
    if needs_classification_review(message):
        return "review"
    return effective_classification(message) or "unclassified"


def apply_message_classification(
    conversation: Conversation,
    message: Message,
    *,
    manual: bool = False,
    actor_user_id: int | None = None,
) -> None:
    """Update the conversation queue without letting a courtesy reply close a demand."""
    category = _queue_label(message)
    current = conversation.active_classification

    if manual:
        if category == "demand":
            conversation.active_classification = "demand"
            conversation.active_classification_message_id = message.id
            conversation.classification_open = True
            conversation.classification_resolved_at = None
            conversation.classification_resolved_by_id = None
        elif category == "guest_service":
            if not (
                conversation.classification_open
                and current == "demand"
                and conversation.active_classification_message_id != message.id
            ):
                conversation.active_classification = "guest_service"
                conversation.active_classification_message_id = message.id
                conversation.classification_open = True
                conversation.classification_resolved_at = None
                conversation.classification_resolved_by_id = None
        elif conversation.active_classification_message_id == message.id:
            conversation.active_classification = "non_demand"
            conversation.active_classification_message_id = message.id
            conversation.classification_open = False
            conversation.classification_resolved_at = now()
            conversation.classification_resolved_by_id = actor_user_id
        return

    if category == "demand":
        conversation.active_classification = "demand"
        conversation.active_classification_message_id = message.id
        conversation.classification_open = True
        conversation.classification_resolved_at = None
        conversation.classification_resolved_by_id = None
    elif category == "guest_service":
        if not (conversation.classification_open and current == "demand"):
            conversation.active_classification = "guest_service"
            conversation.active_classification_message_id = message.id
            conversation.classification_open = True
            conversation.classification_resolved_at = None
            conversation.classification_resolved_by_id = None
    elif category == "review":
        if not (
            conversation.classification_open
            and current in {"demand", "guest_service"}
        ):
            conversation.active_classification = "review"
            conversation.active_classification_message_id = message.id
            conversation.classification_open = True
            conversation.classification_resolved_at = None
            conversation.classification_resolved_by_id = None
    elif not conversation.classification_open:
        conversation.active_classification = "non_demand"
        conversation.active_classification_message_id = message.id


def resolve_conversation_classification(
    conversation: Conversation, *, outcome: str, actor_user_id: int
) -> None:
    conversation.classification_open = False
    conversation.classification_resolved_at = now()
    conversation.classification_resolved_by_id = actor_user_id
    if outcome == "not_demand":
        conversation.active_classification = "non_demand"
        conversation.active_classification_message_id = None
