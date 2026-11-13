"""Out-of-hours acknowledgement for new customer enquiries.

A customer who writes at night or at the weekend asking for information gets a short, fixed
message saying the request cannot be handled right now. Only the first message of a brand-new
email thread qualifies: replies, forwards, quoted histories, colleagues, robots and anything
the classifier is not sure is a demand never get one.

AUTO_REPLY_MODE decides what happens to an eligible message:
- off: nothing.
- shadow: the acknowledgement is recorded (auto_replies + audit) but never sent.
- send: it is sent by SMTP. This is the only outbound path allowed while READ_ONLY_MODE
  blocks drafts, alerts and WhatsApp; nothing else is unblocked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from email.utils import parseaddr
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.availability import aware
from app.config import get_settings
from app.contacts import (
    FORWARD_HEADER_RE,
    FORWARD_SUBJECT_RE,
    QUOTE_START_RE,
    ROLE_LOCALS,
    _domains,
    _is_internal,
)
from app.delivery import reply_subject
from app.models import AuditEvent, AutoReply, Conversation, Message

MODES = {"off", "shadow", "send"}
RATE_WINDOW = timedelta(hours=24)

TEMPLATES = {
    "es": (
        "Hola:\n\n"
        "Gracias por escribirnos. Hemos recibido su consulta fuera de nuestro horario de "
        "atención, por lo que en este momento no podemos atenderla. Le responderemos lo antes "
        "posible en cuanto retomemos la actividad.\n\n"
        "Este es un mensaje automático; no es necesario que lo conteste.\n\n"
        "Un saludo."
    ),
    "en": (
        "Hello,\n\n"
        "Thank you for your message. We received your enquiry outside our office hours, so we "
        "are unable to deal with it right now. We will reply as soon as possible when we are "
        "back.\n\n"
        "This is an automatic message; there is no need to reply to it.\n\n"
        "Kind regards."
    ),
    "fr": (
        "Bonjour,\n\n"
        "Merci pour votre message. Nous avons reçu votre demande en dehors de nos heures "
        "d'ouverture et ne pouvons pas la traiter pour le moment. Nous vous répondrons dès que "
        "possible à notre retour.\n\n"
        "Ceci est un message automatique ; inutile d'y répondre.\n\n"
        "Cordialement."
    ),
    "de": (
        "Hallo,\n\n"
        "vielen Dank für Ihre Nachricht. Ihre Anfrage ist außerhalb unserer Bürozeiten "
        "eingegangen, daher können wir sie im Moment nicht bearbeiten. Wir antworten Ihnen so "
        "bald wie möglich.\n\n"
        "Dies ist eine automatische Nachricht; Sie müssen nicht darauf antworten.\n\n"
        "Mit freundlichen Grüßen."
    ),
    "it": (
        "Buongiorno,\n\n"
        "grazie per averci scritto. Abbiamo ricevuto la sua richiesta fuori dal nostro orario "
        "di lavoro, quindi al momento non possiamo gestirla. Le risponderemo il prima possibile "
        "alla ripresa dell'attività.\n\n"
        "Questo è un messaggio automatico; non è necessario rispondere.\n\n"
        "Cordiali saluti."
    ),
    "pt": (
        "Olá,\n\n"
        "Obrigado pela sua mensagem. Recebemos o seu pedido fora do nosso horário de "
        "atendimento, pelo que neste momento não o podemos tratar. Responderemos o mais "
        "rapidamente possível quando retomarmos a atividade.\n\n"
        "Esta é uma mensagem automática; não é necessário responder.\n\n"
        "Com os melhores cumprimentos."
    ),
}


@dataclass(frozen=True)
class Decision:
    eligible: bool
    reason: str
    # False for reasons that apply to most mail (not a demand, office hours): not audited.
    audit: bool = True


def mode() -> str:
    value = (get_settings().auto_reply_mode or "off").strip().lower()
    return value if value in MODES else "off"


def _clock(value: str) -> time:
    hours, _, minutes = value.strip().partition(":")
    return time(int(hours), int(minutes or 0))


def is_out_of_hours(moment: datetime) -> bool:
    """Nights (NIGHT_START..NIGHT_END, local time) and whole Saturdays and Sundays."""
    settings = get_settings()
    local = aware(moment).astimezone(ZoneInfo(settings.auto_reply_timezone))
    if local.weekday() >= 5:
        return True
    start, end = _clock(settings.auto_reply_night_start), _clock(settings.auto_reply_night_end)
    current = local.time()
    if start <= end:
        return start <= current < end
    return current >= start or current < end


def language(message: Message) -> str:
    value = (message.classification_data or {}).get("language")
    if value in TEMPLATES:
        return value
    return "en" if value == "other" else "es"


def compose(lang: str) -> str:
    text = TEMPLATES.get(lang, TEMPLATES["es"])
    signature = get_settings().auto_reply_signature.strip()
    return f"{text}\n{signature}" if signature else text


def recipient(message: Message) -> str:
    return parseaddr(message.sender or "")[1].strip().lower()


def _own_addresses() -> set[str]:
    settings = get_settings()
    return {
        parseaddr(item)[1].lower()
        for item in (settings.smtp_from, settings.imap_user, settings.smtp_user)
        if item and parseaddr(item)[1]
    }


def evaluate(
    db: Session, message: Message, conv: Conversation, *, at: datetime | None = None
) -> Decision:
    settings = get_settings()
    current = aware(at or datetime.now(UTC))
    if mode() == "off":
        return Decision(False, "disabled", audit=False)
    if message.direction != "inbound" or message.channel != "email" or conv.channel != "email":
        return Decision(False, "not_inbound_email", audit=False)
    if conv.kind == "booking":
        return Decision(False, "booking", audit=False)
    if message.classification_override is not None or message.classification_label != "demand":
        return Decision(False, "not_demand", audit=False)
    received = aware(message.created_at or current)
    if not is_out_of_hours(received):
        return Decision(False, "office_hours", audit=False)
    if message.classification_confidence != "high":
        return Decision(False, "low_confidence")

    max_age = timedelta(minutes=settings.auto_reply_max_age_minutes)
    if current - received > max_age:
        return Decision(False, "processed_too_late")
    if message.email_date and received - aware(message.email_date) > max_age:
        return Decision(False, "old_email")

    # Part of an email chain: replies, forwards and quoted histories never qualify.
    if (message.references or "").strip():
        return Decision(False, "thread_headers")
    if FORWARD_SUBJECT_RE.search(message.subject or ""):
        return Decision(False, "reply_or_forward_subject")
    body = message.body or ""
    if QUOTE_START_RE.search(body) or FORWARD_HEADER_RE.search(body):
        return Decision(False, "quoted_history")
    others = db.scalar(
        select(func.count(Message.id)).where(
            Message.conversation_id == conv.id, Message.id != message.id
        )
    )
    if others:
        return Decision(False, "existing_conversation")

    address = recipient(message)
    if not address or "@" not in address:
        return Decision(False, "no_sender")
    if address.split("@", 1)[0] in ROLE_LOCALS:
        return Decision(False, "role_sender")
    if _is_internal(address, _domains(settings.internal_sender_domains)):
        return Decision(False, "internal_sender")
    if address in _own_addresses():
        return Decision(False, "own_address")
    recent = db.scalar(
        select(AutoReply.id).where(
            AutoReply.recipient == address,
            AutoReply.mode.in_(("shadow", "sending", "sent")),
            AutoReply.created_at >= current - RATE_WINDOW,
        )
    )
    if recent is not None:
        return Decision(False, "recently_answered")
    return Decision(True, "eligible")


async def run(db: Session, message_id: int) -> None:
    current_mode = mode()
    if current_mode == "off":
        return
    message = db.get(Message, message_id)
    if not message:
        return
    conv = db.get(Conversation, message.conversation_id)
    if not conv:
        return
    if db.scalar(select(AutoReply.id).where(AutoReply.message_id == message.id)) is not None:
        return  # Already decided; a retried job never sends twice.
    decision = evaluate(db, message, conv)
    if not decision.eligible:
        if decision.audit:
            db.add(
                AuditEvent(
                    conversation_id=conv.id,
                    action="auto_reply.skipped",
                    detail={"message_id": message.id, "reason": decision.reason},
                )
            )
            db.commit()
        return

    lang = language(message)
    reply = AutoReply(
        message_id=message.id,
        conversation_id=conv.id,
        recipient=recipient(message),
        mode="shadow" if current_mode == "shadow" else "sending",
        language=lang,
        subject=reply_subject(message.subject)[:500],
        text=compose(lang),
    )
    db.add(reply)
    if current_mode == "shadow":
        db.add(
            AuditEvent(
                conversation_id=conv.id,
                action="auto_reply.shadow",
                detail={"message_id": message.id, "language": lang},
            )
        )
        db.commit()
        return
    db.commit()  # From here a crash leaves "sending": it is never retried automatically.
    await _send(db, reply.id)


async def _send(db: Session, reply_id: int) -> None:
    from app.integrations import smtp_send_auto_reply

    reply = db.get(AutoReply, reply_id)
    message = db.get(Message, reply.message_id)
    domain = (parseaddr(get_settings().smtp_from)[1] or "avantia.local").rsplit("@", 1)[-1]
    provider_id = f"<avantia-auto-{reply.id}@{domain}>"
    try:
        smtp_send_auto_reply(
            to=reply.recipient,
            subject=reply.subject,
            text=reply.text,
            message_id=provider_id,
            in_reply_to=message.provider_id,
            references=message.provider_id,
        )
    except Exception as exc:
        db.rollback()
        reply = db.get(AutoReply, reply_id)
        reply.mode = "failed"
        reply.error = f"{type(exc).__name__}: {str(exc)[:300]}"
        db.add(
            AuditEvent(
                conversation_id=reply.conversation_id,
                action="auto_reply.failed",
                detail={"message_id": reply.message_id, "error_type": type(exc).__name__},
            )
        )
        db.commit()
        return
    reply.mode = "sent"
    reply.provider_id = provider_id
    db.add(
        Message(
            conversation_id=reply.conversation_id,
            channel="email",
            provider_id=provider_id,
            direction="outbound",
            body=reply.text,
            subject=reply.subject,
            sender=get_settings().smtp_from,
            recipient=reply.recipient,
            reply_to_id=message.provider_id,
        )
    )
    # The operator still has to answer the enquiry: the conversation stays pending.
    db.add(
        AuditEvent(
            conversation_id=reply.conversation_id,
            action="auto_reply.sent",
            detail={"message_id": reply.message_id, "language": reply.language},
        )
    )
    db.commit()
