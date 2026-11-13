"""Out-of-hours acknowledgement for new customer enquiries.

A customer who writes at night or at the weekend asking for information gets a short, warm
message in their language: greeted by name when the From header gives one, told that the
office is closed and when the reservations team will reply. Only the first message of a brand-new
email thread qualifies: replies, forwards, quoted histories, colleagues, robots and anything
the classifier is not sure is a demand never get one.

AUTO_REPLY_MODE decides what happens to an eligible message:
- off: nothing.
- shadow: the acknowledgement is recorded (auto_replies + audit) but never sent.
- send: it is sent by SMTP. This is the only outbound path allowed while READ_ONLY_MODE
  blocks drafts, alerts and WhatsApp; nothing else is unblocked.
"""

from __future__ import annotations

import base64
import html
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from email.utils import parseaddr
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.availability import aware
from app.classification import effective_classification
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

# One block per language. compose() fills in the greeting, {when} and {site}.
TEMPLATES = {
    "es": {
        "greeting": ("Hola {name},", "Hola,"),
        "body": (
            "Muchas gracias por escribirnos y por su interés en Albir Confort.\n\n"
            "Ahora mismo nuestra oficina está cerrada, pero hemos recibido su mensaje "
            "correctamente. {when}, una persona de nuestro equipo de reservas revisará su "
            "consulta y le responderá personalmente."
        ),
        "site": "Mientras tanto, puede ver nuestros apartamentos en {site}.",
        "closing": "Un cordial saludo,\nEquipo de reservas de Albir Confort",
        "when": (
            "Hoy a partir de las {hour}",
            "Mañana a partir de las {hour}",
            "El {day} a partir de las {hour}",
        ),
        "days": ("lunes", "martes", "miércoles", "jueves", "viernes"),
        "zone": "",
    },
    "en": {
        "greeting": ("Hello {name},", "Hello,"),
        "body": (
            "Thank you very much for getting in touch and for your interest in Albir "
            "Confort.\n\n"
            "Our office is closed right now, but your message has reached us safely. {when}, a "
            "member of our reservations team will look at your enquiry and reply to you "
            "personally."
        ),
        "site": "In the meantime, you can browse our apartments at {site}.",
        "closing": "Kind regards,\nThe Albir Confort reservations team",
        "when": ("Today from {hour}", "Tomorrow from {hour}", "On {day} from {hour}"),
        "days": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday"),
        "zone": " (Spanish time)",
    },
    "fr": {
        "greeting": ("Bonjour {name},", "Bonjour,"),
        "body": (
            "Merci beaucoup pour votre message et pour l'intérêt que vous portez à Albir "
            "Confort.\n\n"
            "Notre bureau est fermé pour le moment, mais nous avons bien reçu votre message. "
            "{when}, un membre de notre équipe de réservations étudiera votre demande et vous "
            "répondra personnellement."
        ),
        "site": "En attendant, vous pouvez découvrir nos appartements sur {site}.",
        "closing": "Bien cordialement,\nL'équipe de réservations d'Albir Confort",
        "when": (
            "Aujourd'hui à partir de {hour}",
            "Demain à partir de {hour}",
            "{day} à partir de {hour}",
        ),
        "days": ("Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"),
        "zone": " (heure espagnole)",
    },
    "de": {
        "greeting": ("Hallo {name},", "Guten Tag,"),
        "body": (
            "vielen Dank für Ihre Nachricht und Ihr Interesse an Albir Confort.\n\n"
            "Unser Büro ist gerade geschlossen, aber Ihre Nachricht ist gut bei uns "
            "angekommen. {when} wird sich ein Mitglied unseres Reservierungsteams Ihre Anfrage "
            "ansehen und Ihnen persönlich antworten."
        ),
        "site": "In der Zwischenzeit finden Sie unsere Apartments auf {site}.",
        "closing": "Herzliche Grüße\nIhr Reservierungsteam von Albir Confort",
        "when": ("Heute ab {hour} Uhr", "Morgen ab {hour} Uhr", "Am {day} ab {hour} Uhr"),
        "days": ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag"),
        "zone": " (spanische Zeit)",
    },
    "it": {
        "greeting": ("Buongiorno {name},", "Buongiorno,"),
        "body": (
            "grazie mille per averci scritto e per il suo interesse per Albir Confort.\n\n"
            "Il nostro ufficio è chiuso in questo momento, ma abbiamo ricevuto correttamente il "
            "suo messaggio. {when} una persona del nostro team prenotazioni esaminerà la sua "
            "richiesta e le risponderà personalmente."
        ),
        "site": "Nel frattempo, può scoprire i nostri appartamenti su {site}.",
        "closing": "Cordiali saluti,\nIl team prenotazioni di Albir Confort",
        "when": ("Oggi dalle {hour}", "Domani dalle {hour}", "{day} dalle {hour}"),
        "days": ("Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì"),
        "zone": " (ora spagnola)",
    },
    "pt": {
        "greeting": ("Olá {name},", "Olá,"),
        "body": (
            "Muito obrigado pela sua mensagem e pelo seu interesse no Albir Confort.\n\n"
            "Neste momento o nosso escritório está fechado, mas recebemos a sua mensagem. "
            "{when}, uma pessoa da nossa equipa de reservas irá analisar o seu pedido e "
            "responder-lhe pessoalmente."
        ),
        "site": "Entretanto, pode ver os nossos apartamentos em {site}.",
        "closing": "Com os melhores cumprimentos,\nA equipa de reservas do Albir Confort",
        "when": (
            "Hoje a partir das {hour}",
            "Amanhã a partir das {hour}",
            "Na {day} a partir das {hour}",
        ),
        "days": ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira"),
        "zone": " (hora de Espanha)",
    },
    # Norwegian (bokmål). Norway shares Spain's time zone, so no zone note.
    "nb": {
        "greeting": ("Hei {name},", "Hei,"),
        "body": (
            "Tusen takk for at du tok kontakt, og for din interesse for Albir Confort.\n\n"
            "Kontoret vårt er stengt akkurat nå, men vi har mottatt meldingen din. {when} vil "
            "en av oss i reservasjonsteamet se på henvendelsen din og svare deg personlig."
        ),
        "site": "I mellomtiden kan du se leilighetene våre på {site}.",
        "closing": "Med vennlig hilsen\nReservasjonsteamet i Albir Confort",
        "when": ("I dag fra kl. {hour}", "I morgen fra kl. {hour}", "{day} fra kl. {hour}"),
        "days": ("Mandag", "Tirsdag", "Onsdag", "Torsdag", "Fredag"),
        "zone": "",
    },
}
# Words that sit where a first name would in a From header but are not names.
NOT_NAMES = {
    "info", "reservas", "reservations", "booking", "bookings", "admin", "contact", "contacto",
    "hello", "hola", "office", "oficina", "team", "equipo", "sales", "ventas", "mail", "email",
    "familia", "family", "famille", "familie", "famiglia", "hotel", "hostal", "viajes",
    "travel", "travels", "tours", "agencia", "agency", "apartamentos", "apartments", "holidays",
    "vacaciones", "rentals", "service", "services", "servicio", "support", "soporte", "noreply",
    "extranet", "product", "products", "integrations", "integration", "gmail", "hotmail",
    "outlook", "yahoo", "icloud", "grup", "group", "grupo", "marketing", "news", "newsletter",
    "customer", "customers", "cliente", "clientes", "real", "estate", "inmobiliaria",
}
# Letters that Unicode does not split into base letter + accent, as people type them in emails.
LETTERS_WITHOUT_ACCENT_FORM = str.maketrans(
    {"ø": "o", "æ": "ae", "œ": "oe", "ð": "d", "þ": "th", "ł": "l", "đ": "d", "ß": "ss"}
)
NAME_RE = re.compile(r"[^\W\d_]+(?:[-'’][^\W\d_]+)*")


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
    """The template language: the one the model detected, English for any other language.

    When the model gave no language (it failed or answered something unusable), Spanish only
    for a Spanish address (.es) and English for everyone else, so that a foreign customer
    never gets a reply in Spanish by accident.
    """
    data = message.classification_data or {}
    value = data.get("language")
    if data.get("language_detected") is False or value is None:
        domain = recipient(message).rsplit("@", 1)[-1]
        return "es" if domain.endswith(".es") else "en"
    if value in TEMPLATES:
        return value
    return "en"


def _fold(value: str) -> str:
    """Lowercase without accents or separators, to compare a name with an email address."""
    value = value.lower().translate(LETTERS_WITHOUT_ACCENT_FORM)
    decomposed = unicodedata.normalize("NFKD", value)
    return re.sub(r"[^a-z]", "", "".join(c for c in decomposed if not unicodedata.combining(c)))


def first_name(message: Message) -> str | None:
    """The customer's first name, only when it is clear; otherwise the greeting has no name.

    Clear means: the From display name is a first name and surname (two to four words, only
    letters, no generic words such as "Info", "Familia" or "Hotel"), and the email address
    itself confirms it, because its local part contains the first name or a surname
    ("Dag Tessem" <dtessem@...>, "Ana García" <ana.garcia@...>). A single word, a company or
    a name the address does not back up ("Viajes Sol" <info@...>) gets no name.
    """
    display, address = parseaddr(message.sender or "")
    display = display.strip().strip("\"'").strip()
    if not display or "@" in display or "@" not in address:
        return None
    if "," in display:  # "Surname, Name"
        surname, given = display.split(",", 1)
        display = f"{given.strip()} {surname.strip()}"
    words = display.split()
    # A first name and at least one surname: a single word is too often a username copy
    # ("Mibtrysil" <mibtrysil@...>) or not a person at all.
    if not 2 <= len(words) <= 4:
        return None
    if any(not NAME_RE.fullmatch(word) or word.lower() in NOT_NAMES for word in words):
        return None
    token = words[0]
    given = _fold(token)
    if len(given) < 2:
        return None
    local_part, domain = address.rsplit("@", 1)
    local = _fold(local_part)
    # A word of the name in the domain means a company ("Product Muchosol" <...@muchosol.com>).
    domain_name = _fold(domain.lower().split(".")[0]) if "." in domain else ""
    if domain_name and any(len(_fold(w)) >= 4 and _fold(w) in domain_name for w in words):
        return None
    # The address has to back up the first name itself: it contains it ("hilde.straume"),
    # or it is the initial plus a surname ("dtessem" for Dag Tessem).
    surnames = [_fold(word) for word in words[1:] if len(_fold(word)) >= 3]
    confirmed = (len(given) >= 3 and given in local) or (
        local.startswith(given[0]) and any(surname in local for surname in surnames)
    )
    if not confirmed:
        return None
    return token.capitalize() if token.isupper() or token.islower() else token


def reopening(received: datetime) -> datetime:
    """When the office opens next after this moment: NIGHT_END on the next weekday."""
    settings = get_settings()
    local = aware(received).astimezone(ZoneInfo(settings.auto_reply_timezone))
    opening = _clock(settings.auto_reply_night_end)
    candidate = local.replace(hour=opening.hour, minute=opening.minute, second=0, microsecond=0)
    if local >= candidate:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def _when(lang: str, received: datetime) -> str:
    template = TEMPLATES[lang]
    local = aware(received).astimezone(ZoneInfo(get_settings().auto_reply_timezone))
    opens = reopening(received)
    days = (opens.date() - local.date()).days
    hour = f"{opens.hour}:{opens.minute:02d}"
    if lang == "fr":
        hour = f"{opens.hour}h{opens.minute:02d}" if opens.minute else f"{opens.hour}h"
    elif lang == "nb":
        hour = f"{opens.hour:02d}.{opens.minute:02d}"
    today, tomorrow, other = template["when"]
    pattern = today if days == 0 else tomorrow if days == 1 else other
    return pattern.format(hour=hour, day=template["days"][opens.weekday()]) + template["zone"]


def _site() -> str | None:
    url = get_settings().auto_reply_banner_url.strip()
    return re.sub(r"^https?://", "", url).strip("/") or None


def compose(lang: str, *, name: str | None = None, received: datetime | None = None) -> str:
    lang = lang if lang in TEMPLATES else "es"
    template = TEMPLATES[lang]
    with_name, without_name = template["greeting"]
    parts = [
        with_name.format(name=name) if name else without_name,
        template["body"].format(when=_when(lang, received or datetime.now(UTC))),
    ]
    site = _site()
    if site:
        parts.append(template["site"].format(site=site))
    parts.append(template["closing"])
    signature = get_settings().auto_reply_signature.strip()
    if signature:
        parts.append(signature)
    return "\n\n".join(parts)


BANNER = Path(__file__).parent / "assets" / "auto_reply_banner.png"


@lru_cache
def _banner_data_uri() -> str | None:
    try:
        return "data:image/png;base64," + base64.b64encode(BANNER.read_bytes()).decode()
    except OSError:
        return None


def compose_html(text: str) -> str:
    """The acknowledgement as the team's Outlook replies look: text, then the company banner."""
    from app.html_sanitize import text_to_html

    settings = get_settings()
    body = text_to_html(text)
    banner = _banner_data_uri() if settings.auto_reply_banner else None
    if banner:
        image = (
            f'<img src="{banner}" width="589" height="94" alt="Albir Confort Holidays" '
            'style="border:0;display:block;max-width:100%;height:auto">'
        )
        url = settings.auto_reply_banner_url.strip()
        if url:
            image = f'<a href="{html.escape(url)}">{image}</a>'
        body += f"<p>{image}</p>"
    return body


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
    db: Session,
    message: Message,
    conv: Conversation,
    *,
    at: datetime | None = None,
    simulation: dict[str, datetime] | None = None,
    any_hour: bool = False,
) -> Decision:
    """Whether this message gets an acknowledgement.

    With ``simulation`` (address -> last simulated reply) the history of the inbox is replayed:
    the mode switch and the processing-delay checks are ignored, and the 24-hour limit uses the
    simulated replies instead of the auto_replies table. ``any_hour`` skips the office-hours
    check, to judge the content filters on history whose arrival time is not reliable.
    """
    settings = get_settings()
    current = aware(at or datetime.now(UTC))
    if simulation is None and mode() == "off":
        return Decision(False, "disabled", audit=False)
    if message.direction != "inbound" or message.channel != "email" or conv.channel != "email":
        return Decision(False, "not_inbound_email", audit=False)
    if conv.kind == "booking":
        return Decision(False, "booking", audit=False)
    # An operator's correction wins over the model; a confirmed demand needs no confidence.
    if effective_classification(message) != "demand":
        return Decision(False, "not_demand", audit=False)
    received = aware(message.created_at or current)
    if not any_hour and not is_out_of_hours(received):
        return Decision(False, "office_hours", audit=False)
    if message.classification_override is None and message.classification_confidence != "high":
        return Decision(False, "low_confidence")

    max_age = timedelta(minutes=settings.auto_reply_max_age_minutes)
    if simulation is None and current - received > max_age:
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
            Message.conversation_id == conv.id, Message.id < message.id
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
    if simulation is not None:
        last = simulation.get(address)
        recent = last if last is not None and received - last < RATE_WINDOW else None
    else:
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
        text=compose(lang, name=first_name(message), received=aware(message.created_at)),
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
        html_body = compose_html(reply.text)
        sent = smtp_send_auto_reply(
            to=reply.recipient,
            subject=reply.subject,
            text=reply.text,
            html_body=html_body,
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
            html_body=html_body,
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
    _keep_sent_copy(db, reply, sent)


def _keep_sent_copy(db: Session, reply: AutoReply, sent) -> None:
    """Copy to the mailbox's sent folder, so the team sees it in Outlook. Best effort."""
    from app.mail import append_sent_copy

    folder = get_settings().auto_reply_sent_folder.strip()
    if not folder:
        return
    try:
        append_sent_copy(sent, folder)
    except Exception as exc:
        db.add(
            AuditEvent(
                conversation_id=reply.conversation_id,
                action="auto_reply.copy_failed",
                detail={"message_id": reply.message_id, "error_type": type(exc).__name__},
            )
        )
        db.commit()


def simulate(
    db: Session, *, start: datetime, end: datetime, any_hour: bool = False
) -> dict:
    """Replay the stored inbox between start and end and report what would have been answered.

    Read-only: nothing is written, nothing is sent. The checks are the live ones, with two
    differences: the processing delay is ignored (the history was processed long ago) and an
    email is judged by the messages that existed when it arrived, not by later replies.
    """
    messages = db.scalars(
        select(Message)
        .where(
            Message.channel == "email",
            Message.direction == "inbound",
            Message.created_at >= start,
            Message.created_at < end,
        )
        .order_by(Message.created_at, Message.id)
    ).all()
    answered: dict[str, datetime] = {}
    reasons: dict[str, int] = {}
    replies = []
    out_of_hours = 0
    for message in messages:
        conv = db.get(Conversation, message.conversation_id)
        if conv is None:
            continue
        received = aware(message.created_at)
        if is_out_of_hours(received):
            out_of_hours += 1
        decision = evaluate(
            db, message, conv, at=received, simulation=answered, any_hour=any_hour
        )
        reasons[decision.reason] = reasons.get(decision.reason, 0) + 1
        if not decision.eligible:
            continue
        answered[recipient(message)] = received
        replies.append(
            {
                "message_id": message.id,
                "conversation_id": conv.id,
                "received_at": received,
                "recipient": recipient(message),
                "subject": message.subject,
                "language": language(message),
                "excerpt": (message.body or "")[:300],
            }
        )
    return {
        "start": start,
        "any_hour": any_hour,
        "end": end,
        "total": len(messages),
        "out_of_hours": out_of_hours,
        "replies": len(replies),
        "reasons": dict(sorted(reasons.items(), key=lambda item: -item[1])),
        "items": replies,
    }


def main() -> None:
    """python -m app.auto_reply [días] [--any-hour]: simulate the last N days (90 by default)."""
    import sys

    from app.db import SessionLocal

    args = [item for item in sys.argv[1:] if not item.startswith("--")]
    any_hour = "--any-hour" in sys.argv
    days = int(args[0]) if args else 90
    end = datetime.now(UTC)
    with SessionLocal() as db:
        result = simulate(db, start=end - timedelta(days=days), end=end, any_hour=any_hour)
    tz = ZoneInfo(get_settings().auto_reply_timezone)
    print(
        f"Últimos {days} días: {result['total']} emails entrantes, "
        f"{result['out_of_hours']} fuera de horario, {result['replies']} autorespuestas."
    )
    for reason, count in result["reasons"].items():
        print(f"  {reason}: {count}")
    for item in result["items"]:
        when = item["received_at"].astimezone(tz).strftime("%a %d/%m %H:%M")
        print(f"{when} · {item['language']} · {item['recipient']} · {item['subject'] or ''}")


if __name__ == "__main__":
    main()
