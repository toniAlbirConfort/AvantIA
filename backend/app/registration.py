"""Alert when an email consultation is not registered in Avantio.

After every successful import of Avantio reservations, each new email consultation is checked
against the reservation list. A consultation counts as registered when a reservation in any
status (Petición de información, Confirmada, Pagada, Cancelada...) matches the customer and was
created around the email. Once REGISTRATION_ALERT_DELAY_MINUTES of office time have passed with
no match, the check becomes an alert. REGISTRATION_ALERT_MODE:

- off: nothing is checked.
- shadow: alerts are recorded (registration_checks + audit) but nobody is notified.

The matching rules come from the November 2026 audit of the reservas@ mailbox: Avantio stores
names in capitals with middle names and transliterated letters (Sølvi -> SOELVI), sometimes
surname first, and reservations do not carry the customer's email.
"""

from __future__ import annotations

import argparse
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from email.utils import parseaddr
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.classification import effective_classification
from app.config import get_settings
from app.contacts import message_contact, new_part
from app.models import (
    AppSetting,
    AuditEvent,
    Conversation,
    Message,
    RegistrationCheck,
    ReservationSnapshot,
    ReservationSyncState,
    now,
)

MODES = {"off", "shadow"}
FOLLOW_UP_DAYS = 45
REGISTERED_BEFORE_DAYS = 2
REGISTERED_AFTER_DAYS = 14
UNCLASSIFIED_GRACE = timedelta(minutes=30)
STALE_SYNC_KEY = "registration_sync_stale"

PLATFORM = re.compile(
    r"booking\.com|airbnb|expedia|vrbo|homeaway|avantio|holidu|hotelbeds|hbxgroup|belvilla|"
    r"interhome|poplidays|maeva|pricelabs|rentalsunited|housinganywhere|noreply|no-reply|"
    r"donotreply|mailer-daemon|postmaster|guestincoming|sidetours|mariannetours",
    re.IGNORECASE,
)
REPLY_SUBJECT = re.compile(r"^\s*(re|rv|sv|aw|antw|vs|fw|fwd|wg|tr)\s*:", re.IGNORECASE)
INVITE = re.compile(r"^\s*(updated\s+)?(invitation|invitaci[oó]n|einladung|invitasjon)\b", re.I)
TEMPLATE = re.compile(
    r"petici[oó]n\s+(?:de\s+)?informaci[oó]n\s+apartamento|request\s+booking\s+information|"
    r"booking\s+confirmation|pre\s*booking\s+apartment|confirmaci[oó]n\s+(?:pre\s*)?reserva",
    re.IGNORECASE,
)
ACCOM = re.compile(
    r"apart|appart|leilighet|lejlighed|wohnung|accommodat|alojamiento|piso\b|flat\b|villa|"
    r"estancia|\bstay|opphold|ophold|holiday|vacation|vacacion|ferie|urlaub|vakantie|book|reserv|"
    r"bestill|ledig|availab|disponib|alquil|\brent\b|\brenting\b|leie\b|room|habitaci|dormitor|"
    r"soverom|bedroom|night|noche|natt|week|semana|uke|price|precio|pris\b|prijs|offer|oferta|"
    r"tilbud",
    re.IGNORECASE,
)
DATE_RANGE = re.compile(
    r"\d{1,2}(?:[./]\d{1,2})?\.?\s*(?:-|–|al|to|til|till|until|hasta|bis|tot)\s*\d{1,2}", re.I
)
EXISTING_GUEST = re.compile(
    r"\b(reserva(tion)?\s*(no|nr|n[ºo°])|booking\s*(ref|no|number|nr)|reservasjonsnr|bestilt|"
    r"booked|have\s+booked|hemos\s+reservado|tenemos\s+(una\s+)?reserva|stayed|just\s+left|"
    r"our\s+stay|nuestra\s+estancia|oppholdet|deposit|depositum|"
    r"cancel\w*\s+(?:of\s+)?(?:my|our|the|this|booking|reservation|reserva|mi|nuestra)|"
    r"cancelar\s+(?:la|mi|nuestra)|avbestill|arrival\s+details|check.?in|llegada|key|n[oø]kkel)",
    re.IGNORECASE,
)
FORM_SURNAME = re.compile(
    r"(?:last\s*names?|etternavn|apellidos?|nachname|achternaam)\s*:\s*([^\n:]{2,40}?)\s+"
    r"(?:e-?mail|e-post|correo|phone|telefon|tel)",
    re.IGNORECASE,
)
LOCATOR_NUMBER = re.compile(r"\b(\d{7,9})(?:-\d{5,10})?\b")
URL = re.compile(r"<?https?://\S+|www\.\S+")
SPECIAL = str.maketrans(
    {"ø": "oe", "Ø": "oe", "æ": "ae", "Æ": "ae", "å": "aa", "Å": "aa", "ß": "ss"}
)
SIGN_OFF = {
    "mvh",
    "hilsen",
    "regards",
    "kind",
    "best",
    "sent",
    "from",
    "my",
    "iphone",
    "ipad",
    "saludos",
    "gracias",
    "thanks",
    "thank",
    "you",
    "met",
    "vriendelijke",
    "groet",
    "groeten",
}
NOT_A_NAME = {"de", "van", "der", "den", "la", "el", "von", "y", "and", "mr", "mrs", "ms"}


def alert_mode() -> str:
    value = (get_settings().registration_alert_mode or "off").strip().lower()
    return value if value in MODES else "off"


# --- names ---------------------------------------------------------------------------------


def fold(value: str | None) -> str:
    decomposed = unicodedata.normalize("NFKD", (value or "").translate(SPECIAL))
    return "".join(char for char in decomposed if not unicodedata.combining(char)).lower()


def words(value: str | None) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z]+", fold(value))
        if len(token) >= 3 and token not in NOT_A_NAME
    ]


def skeleton(token: str) -> str:
    """Spelling-tolerant form: Sørgaard, Sorgaard and SOERGAARD all become sorgard."""
    for old, new in (("oe", "o"), ("ae", "a"), ("aa", "a"), ("ue", "u")):
        token = token.replace(old, new)
    return token


def surnames(name: str | None) -> set[str]:
    """Every word but the first name; a single word is kept as it is."""
    tokens = [token for token in words(name) if token not in SIGN_OFF]
    return set(tokens[1:]) if len(tokens) >= 2 else set(tokens)


@dataclass
class Identity:
    surnames: set[str] = field(default_factory=set)  # skeletons, at least 4 letters
    first_name: str = ""
    local_part: str = ""  # email before @, letters only
    signature: set[str] = field(default_factory=set)
    has_name: bool = False
    known_locators: set[str] = field(default_factory=set)


def identity_for(name: str | None, email: str | None, text: str) -> Identity:
    tokens = surnames(name)
    form = FORM_SURNAME.search(text or "")
    if form:
        tokens |= set(words(form.group(1)))
    lines = new_part(text or "").strip().splitlines()[-3:]
    signature = {skeleton(token) for token in words(" ".join(lines)[-160:]) if len(token) >= 5}
    name_words = [token for token in words(name) if token not in SIGN_OFF]
    local = re.sub(r"[^a-z]", "", fold((email or "").split("@")[0]))
    return Identity(
        surnames={skeleton(token) for token in tokens if len(token) >= 4},
        first_name=skeleton(name_words[0]) if name_words else "",
        local_part=local,
        signature=signature,
        has_name=bool(name_words),
    )


@dataclass
class Reservation:
    locator: str
    status: str
    created_on: date | None
    start_date: date
    end_date: date
    display_name: str
    tokens: set[str]  # skeletons of every name word, at least 4 letters
    last: str  # last name word, folded

    @property
    def prefix(self) -> str:
        return self.locator.split("-")[0]


def reservation_from(row: ReservationSnapshot) -> Reservation:
    name_words = words(row.display_name)
    return Reservation(
        locator=row.locator,
        status=row.status,
        created_on=row.created_on,
        start_date=row.start_date,
        end_date=row.end_date,
        display_name=row.display_name,
        tokens={skeleton(token) for token in name_words if len(token) >= 4},
        last=name_words[-1] if name_words else "",
    )


def same_person(reservation: Reservation, who: Identity) -> bool:
    """Surname shared, or the reservation's surname spelled in the email or the signature."""
    if reservation.tokens & who.surnames:
        return True
    last = skeleton(reservation.last)
    return len(reservation.last) >= 5 and (
        last in skeleton(who.local_part) or last in who.signature
    )


def is_current_guest(reservation: Reservation, who: Identity) -> bool:
    """Stricter than same_person: a shared surname alone is too common to say 'already a guest'."""
    if reservation.locator in who.known_locators:
        return True
    if not who.has_name:
        # "gthyge@" for THYGESEN: only when the email carries no name to compare with.
        last = reservation.last
        return len(last) >= 6 and last[:5] in who.local_part
    return same_person(reservation, who) and who.first_name in {
        skeleton(token) for token in words(reservation.display_name)
    }


# --- office time ---------------------------------------------------------------------------


def _clock(value: str) -> tuple[int, int]:
    hours, _, minutes = value.strip().partition(":")
    return int(hours), int(minutes or 0)


def office_deadline(received: datetime, minutes: int) -> datetime:
    """``minutes`` of office time after ``received`` (nights and weekends do not count).

    Office hours are the complement of the auto-reply night (AUTO_REPLY_NIGHT_END to
    AUTO_REPLY_NIGHT_START, Monday to Friday, AUTO_REPLY_TIMEZONE).
    """
    settings = get_settings()
    zone = ZoneInfo(settings.auto_reply_timezone)
    open_h, open_m = _clock(settings.auto_reply_night_end)
    close_h, close_m = _clock(settings.auto_reply_night_start)
    moment = _aware(received).astimezone(zone)
    remaining = timedelta(minutes=minutes)
    for _ in range(400):  # bounded: at most a few weeks of office days
        opening = moment.replace(hour=open_h, minute=open_m, second=0, microsecond=0)
        closing = moment.replace(hour=close_h, minute=close_m, second=0, microsecond=0)
        if moment.weekday() >= 5 or moment >= closing:
            moment = (opening + timedelta(days=1)) if moment >= opening else opening
            while moment.weekday() >= 5:
                moment += timedelta(days=1)
            continue
        if moment < opening:
            moment = opening
        available = closing - moment
        if remaining <= available:
            return (moment + remaining).astimezone(UTC)
        remaining -= available
        moment = opening + timedelta(days=1)
    return (moment + remaining).astimezone(UTC)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


# --- decision ------------------------------------------------------------------------------


@dataclass
class Decision:
    status: str  # skip (not yet decidable), excluded, registered, pending
    reason: str | None = None
    locator: str | None = None


def _sender_email(message: Message) -> str:
    return parseaddr(message.sender or "")[1].lower()


def _internal(address: str) -> bool:
    domains = [
        item.strip().lower()
        for item in get_settings().internal_sender_domains.split(",")
        if item.strip()
    ]
    domain = address.rsplit("@", 1)[-1]
    return any(domain == item or domain.endswith("." + item) for item in domains)


def _booking_platform(address: str) -> bool:
    domains = [
        item.strip().lower()
        for item in get_settings().booking_sender_domains.split(",")
        if item.strip()
    ]
    domain = address.rsplit("@", 1)[-1]
    return bool(PLATFORM.search(address)) or any(
        domain == item or domain.endswith("." + item) for item in domains
    )


def customer_of(
    message: Message, conversation: Conversation
) -> tuple[str | None, str | None, bool]:
    """(email, name, relayed) of the customer behind the first message of a conversation."""
    contact = message_contact(message, conversation.channel) or {}
    if contact.get("source") == "relayed":
        emails = contact.get("emails") or []
        return (emails[0] if emails else None), (contact.get("name") or message.subject), True
    sender_name, sender_address = parseaddr(message.sender or "")
    return (
        sender_address.lower() or None,
        sender_name or conversation.customer_name,
        False,
    )


def decide(
    db: Session,
    message: Message,
    conversation: Conversation,
    reservations: list[Reservation],
    current_time: datetime,
) -> Decision:
    """Classify the first message of a conversation. Pure apart from read-only queries."""
    sender = _sender_email(message)
    email, name, relayed = customer_of(message, conversation)
    if not relayed and (_internal(sender) or _booking_platform(sender)):
        return Decision("excluded", "plataforma_o_interno")
    if relayed and not email:
        return Decision("excluded", "interno_sin_cliente")
    if email and _booking_platform(email):
        return Decision("excluded", "plataforma_o_interno")

    label = effective_classification(message)
    if label is None and not relayed:
        received = _aware(message.created_at)
        if current_time - received < UNCLASSIFIED_GRACE:
            return Decision("skip")  # give the classifier a chance first
    if label == "non_demand" and not relayed:
        return Decision("excluded", "no_demanda")
    if label == "guest_service" and not relayed:
        return Decision("excluded", "atencion_huesped")
    if not relayed and REPLY_SUBJECT.search(message.subject or ""):
        return Decision("excluded", "respuesta")

    text = f"{message.subject or ''}\n{new_part(message.body or '')}"
    plain = URL.sub(" ", text)
    if INVITE.search(message.subject or "") or not (
        ACCOM.search(plain) or DATE_RANGE.search(plain)
    ):
        return Decision("excluded", "no_consulta")

    received = _aware(message.email_date or message.created_at)
    if email:
        earlier = db.scalar(
            select(RegistrationCheck.id).where(
                RegistrationCheck.customer_email == email,
                RegistrationCheck.conversation_id != conversation.id,
                # Only an earlier consultation makes this a follow-up, never an excluded email.
                RegistrationCheck.status.in_(("pending", "alerted", "registered")),
                RegistrationCheck.received_at < received,
                RegistrationCheck.received_at >= received - timedelta(days=FOLLOW_UP_DAYS),
            )
        )
        if earlier is not None:
            return Decision("excluded", "seguimiento")

    who = customer_identity(db, message, email, name, reservations, current_time)
    found = registration_for(db, email, who, received, reservations)
    if found:
        return found
    day = received.date()
    if any(
        item.status in {"paid", "confirmed", "pre_reservation"}
        and item.created_on is not None
        and item.created_on < day
        and item.end_date >= day
        and is_current_guest(item, who)
        for item in reservations
    ):
        return Decision("excluded", "huesped_con_reserva")
    if EXISTING_GUEST.search(text):
        return Decision("excluded", "parece_huesped")
    return Decision("pending")


def customer_identity(
    db: Session,
    message: Message,
    email: str | None,
    name: str | None,
    reservations: list[Reservation],
    current_time: datetime,
) -> Identity:
    who = identity_for(name, email, message.body or "")
    who.known_locators = known_locators(db, email, reservations, current_time)
    by_locator = {item.locator: item for item in reservations}
    for locator in who.known_locators:  # learn who this address is: surnames, never first names
        item = by_locator.get(locator)
        if item:
            who.surnames |= {skeleton(t) for t in words(item.display_name)[1:] if len(t) >= 4}
    return who


def registration_for(
    db: Session,
    email: str | None,
    who: Identity,
    received: datetime,
    reservations: list[Reservation],
) -> Decision | None:
    day = received.date()
    window_start = day - timedelta(days=REGISTERED_BEFORE_DAYS)
    window_end = day + timedelta(days=REGISTERED_AFTER_DAYS)
    matches = [
        item
        for item in reservations
        if item.created_on is not None
        and window_start <= item.created_on <= window_end
        and same_person(item, who)
    ]
    if matches:
        best = min(matches, key=lambda item: abs((item.created_on - day).days))
        return Decision("registered", "reserva", best.locator)
    if email:
        # The customer answering Avantio's own offer ("RE: Petición información apartamento
        # 130651") proves the request was entered, even under another guest name.
        subjects = db.scalars(
            select(Message.subject)
            .where(
                Message.direction == "inbound",
                func.lower(Message.sender).contains(email),
                Message.created_at >= received - timedelta(days=1),
            )
            .limit(50)
        ).all()
        if any(TEMPLATE.search(subject or "") for subject in subjects):
            return Decision("registered", "plantilla_avantio")
    return None


def known_locators(
    db: Session, email: str | None, reservations: list[Reservation], current_time: datetime
) -> set[str]:
    """Reservations cited in any stored message with this address, up to now."""
    if not email:
        return set()
    by_prefix: dict[str, list[str]] = {}
    for item in reservations:
        by_prefix.setdefault(item.prefix, []).append(item.locator)
    texts = db.execute(
        select(Message.subject, Message.body)
        .where(
            or_(
                func.lower(Message.sender).contains(email),
                func.lower(Message.recipient).contains(email),
            ),
            Message.created_at <= current_time,
        )
        .order_by(Message.id.desc())
        .limit(200)
    ).all()
    found: set[str] = set()
    for subject, body in texts:
        for number in LOCATOR_NUMBER.findall(f"{subject or ''} {body or ''}"):
            found.update(by_prefix.get(number, []))
    return found


# --- runs ----------------------------------------------------------------------------------


def latest_reservations(db: Session) -> list[Reservation]:
    """The newest valid Avantio import (each import holds the merged reservation list)."""
    from app.planning import latest_source_batch

    batch = latest_source_batch(db, "reservations")
    if batch is None:
        return []
    rows = db.scalars(select(ReservationSnapshot).where(ReservationSnapshot.batch_id == batch.id))
    return [reservation_from(row) for row in rows if row.status not in {"owner", "unavailable"}]


def first_inbound_messages(db: Session, since: datetime) -> list[tuple[Conversation, Message]]:
    """First inbound email of each conversation received since ``since`` without a check yet."""
    first_ids = (
        select(func.min(Message.id))
        .where(Message.direction == "inbound", Message.channel == "email")
        .group_by(Message.conversation_id)
    )
    checked = select(RegistrationCheck.conversation_id)
    rows = db.execute(
        select(Conversation, Message)
        .join(Message, Message.conversation_id == Conversation.id)
        .where(
            Message.id.in_(first_ids),
            Message.created_at >= since,
            Conversation.id.not_in(checked),
        )
        .order_by(Message.id)
    ).all()
    return [(conversation, message) for conversation, message in rows]


def run_registration_check(db: Session, current_time: datetime | None = None) -> dict:
    """Create checks for new consultations, resolve pending ones and record due alerts."""
    mode = alert_mode()
    summary = {"mode": mode, "created": 0, "registered": 0, "excluded": 0, "alerted": 0}
    if mode == "off":
        return summary
    settings = get_settings()
    current_time = _aware(current_time or now())
    reservations = latest_reservations(db)
    if not reservations:
        summary["skipped"] = "sin_reservas"
        return summary
    since = current_time - timedelta(days=settings.registration_alert_lookback_days)

    for conversation, message in first_inbound_messages(db, since):
        decision = decide(db, message, conversation, reservations, current_time)
        if decision.status == "skip":
            continue
        email, name, _ = customer_of(message, conversation)
        received = _aware(message.email_date or message.created_at)
        check = RegistrationCheck(
            conversation_id=conversation.id,
            message_id=message.id,
            customer_email=email,
            customer_name=(name or "")[:255] or None,
            received_at=received,
            due_at=office_deadline(received, settings.registration_alert_delay_minutes),
            status=decision.status,
            reason=decision.reason,
            locator=decision.locator,
            checked_at=current_time,
            resolved_at=current_time if decision.status in {"registered", "excluded"} else None,
        )
        db.add(check)
        db.flush()
        summary["created"] += 1
        if decision.status in {"registered", "excluded"}:
            summary[decision.status] += 1

    open_checks = db.scalars(
        select(RegistrationCheck).where(
            RegistrationCheck.status.in_(("pending", "alerted")),
            RegistrationCheck.received_at >= since - timedelta(days=REGISTERED_AFTER_DAYS),
        )
    ).all()
    for check in open_checks:
        message = db.get(Message, check.message_id)
        conversation = db.get(Conversation, check.conversation_id)
        email, name, _ = customer_of(message, conversation)
        who = customer_identity(db, message, email, name, reservations, current_time)
        found = registration_for(db, email, who, _aware(check.received_at), reservations)
        check.checked_at = current_time
        if found:
            check.reason = "registrada_tras_aviso" if check.status == "alerted" else found.reason
            check.status = "registered"
            check.locator = found.locator
            check.resolved_at = current_time
            summary["registered"] += 1
            db.add(
                AuditEvent(
                    conversation_id=check.conversation_id,
                    action="registration.resolved",
                    detail={"check_id": check.id, "locator": check.locator, "how": found.reason},
                )
            )
        elif check.status == "pending" and current_time >= _aware(check.due_at):
            check.status = "alerted"
            check.mode = mode
            check.alerted_at = current_time
            summary["alerted"] += 1
            db.add(
                AuditEvent(
                    conversation_id=check.conversation_id,
                    action="registration.alert",
                    detail={"check_id": check.id, "mode": mode},
                )
            )
    db.commit()
    return summary


def enqueue_registration_check(db: Session, batch_id: int) -> None:
    """Called after every valid reservations import (also when the CSV did not change)."""
    if alert_mode() == "off":
        return
    from app.ingest import enqueue

    stamp = now().strftime("%Y%m%d%H%M")
    enqueue(
        db,
        key=f"registration_check:{batch_id}:{stamp}",
        kind="registration_check",
        payload={"batch_id": batch_id},
    )


def watch_sync(db: Session, current_time: datetime | None = None) -> bool:
    """Record once per episode that Avantio data is stale, since no check can run meanwhile."""
    if alert_mode() == "off":
        return False
    state = db.get(ReservationSyncState, "avantio_reservations")
    if state is None or state.last_success_at is None:
        return False
    current_time = _aware(current_time or now())
    hours = state.interval_hours or get_settings().avantio_sync_interval_hours
    last_success = _aware(state.last_success_at)
    if current_time - last_success <= timedelta(hours=2 * hours):
        return False
    marker = db.get(AppSetting, STALE_SYNC_KEY)
    stamp = last_success.isoformat()
    if marker is not None and (marker.value or {}).get("last_success_at") == stamp:
        return False
    if marker is None:
        marker = AppSetting(key=STALE_SYNC_KEY, value={})
        db.add(marker)
    marker.value = {"last_success_at": stamp, "noticed_at": current_time.isoformat()}
    marker.updated_at = current_time
    db.add(
        AuditEvent(
            action="registration.sync_stale",
            detail={"last_success_at": stamp, "interval_hours": hours},
        )
    )
    db.commit()
    return True


def check_view(check: RegistrationCheck) -> dict:
    return {
        "id": check.id,
        "conversation_id": check.conversation_id,
        "message_id": check.message_id,
        "customer_email": check.customer_email,
        "customer_name": check.customer_name,
        "received_at": check.received_at,
        "due_at": check.due_at,
        "status": check.status,
        "reason": check.reason,
        "locator": check.locator,
        "mode": check.mode,
        "checked_at": check.checked_at,
        "alerted_at": check.alerted_at,
        "resolved_at": check.resolved_at,
    }


def simulate(db: Session, days: int, current_time: datetime | None = None) -> dict:
    """Replay the rule over the stored inbox without writing anything."""
    current_time = _aware(current_time or now())
    reservations = latest_reservations(db)
    since = current_time - timedelta(days=days)
    counts: dict[str, int] = {}
    alerts = []
    for conversation, message in first_inbound_messages(db, since):
        decision = decide(db, message, conversation, reservations, current_time)
        key = decision.status if decision.status != "excluded" else f"excluded:{decision.reason}"
        counts[key] = counts.get(key, 0) + 1
        if decision.status == "pending":
            alerts.append({"conversation_id": conversation.id, "subject": message.subject})
    db.rollback()
    return {"days": days, "reservations": len(reservations), "counts": counts, "alerts": alerts}


def main() -> None:
    from app.db import SessionLocal

    parser = argparse.ArgumentParser(description="Simula el aviso de consultas sin registrar")
    parser.add_argument("days", nargs="?", type=int, default=30)
    args = parser.parse_args()
    with SessionLocal() as db:
        result = simulate(db, args.days)
    print(f"Últimos {result['days']} días · {result['reservations']} reservas en Avantio")
    for key, value in sorted(result["counts"].items()):
        print(f"  {key}: {value}")
    for item in result["alerts"]:
        print(f"  avisaría: #{item['conversation_id']} {item['subject'] or ''}")


if __name__ == "__main__":
    main()
