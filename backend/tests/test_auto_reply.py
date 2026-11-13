from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app import auto_reply
from app.config import get_settings
from app.delivery import send_approved
from app.mail import _email_date
from app.models import AuditEvent, AutoReply, Conversation, Job, Message
from app.processing import CLASSIFY_SYSTEM, EXTRACT_SYSTEM, analyze_message

MADRID = ZoneInfo("Europe/Madrid")
# Wednesday 2026-11-11 23:30 in Madrid: a weeknight.
NIGHT = datetime(2026, 11, 11, 23, 30, tzinfo=MADRID).astimezone(UTC)
# Wednesday 2026-11-11 11:00 in Madrid: office hours.
DAY = datetime(2026, 11, 11, 11, 0, tzinfo=MADRID).astimezone(UTC)


@pytest.fixture(autouse=True)
def auto_reply_settings(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "auto_reply_mode", "shadow")
    monkeypatch.setattr(settings, "auto_reply_timezone", "Europe/Madrid")
    monkeypatch.setattr(settings, "auto_reply_night_start", "20:00")
    monkeypatch.setattr(settings, "auto_reply_night_end", "08:00")
    monkeypatch.setattr(settings, "auto_reply_max_age_minutes", 60)
    monkeypatch.setattr(settings, "auto_reply_signature", "")
    monkeypatch.setattr(settings, "internal_sender_domains", "albirconfort.es")
    monkeypatch.setattr(settings, "smtp_from", "reservas@albirconfort.es")
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.test")
    monkeypatch.setattr(settings, "imap_user", "reservas@albirconfort.es")
    return settings


def add_email(
    db,
    *,
    sender="Ana <ana@example.test>",
    subject="Disponibilidad en diciembre",
    body="Hola, ¿tienen apartamento para 2 personas del 10 al 13 de diciembre?",
    references=None,
    received=NIGHT,
    email_date=None,
    label="demand",
    confidence="high",
    language="es",
    kind="inquiry",
    conversation=None,
):
    if conversation is None:
        conversation = Conversation(
            channel="email",
            external_key=f"<m{db.query(Conversation).count() + 1}@example.test>",
            kind=kind,
            customer_address=sender,
        )
        db.add(conversation)
        db.flush()
    message = Message(
        conversation_id=conversation.id,
        channel="email",
        provider_id=f"<msg-{db.query(Message).count() + 1}@example.test>",
        direction="inbound",
        body=body,
        subject=subject,
        sender=sender,
        references=references,
        created_at=received,
        email_date=email_date or received,
        classification_label=label,
        classification_confidence=confidence,
        classification_data={"language": language},
    )
    db.add(message)
    db.commit()
    return conversation, message


def decide(db, message, at=None):
    conv = db.get(Conversation, message.conversation_id)
    at = at or message.created_at + timedelta(minutes=2)
    return auto_reply.evaluate(db, message, conv, at=at)


def test_new_enquiry_at_night_is_eligible(db):
    _, message = add_email(db)
    assert decide(db, message).eligible


@pytest.mark.parametrize(
    ("local", "expected"),
    [
        (datetime(2026, 11, 11, 19, 59), False),  # Wednesday evening, still open.
        (datetime(2026, 11, 11, 20, 0), True),
        (datetime(2026, 11, 12, 7, 59), True),
        (datetime(2026, 11, 12, 8, 0), False),
        (datetime(2026, 11, 13, 20, 1), True),  # Friday night.
        (datetime(2026, 11, 14, 12, 0), True),  # Saturday midday.
        (datetime(2026, 11, 15, 18, 0), True),  # Sunday.
        (datetime(2026, 11, 16, 7, 59), True),  # Monday early morning.
        (datetime(2026, 11, 16, 8, 1), False),
        # Monday after the switch to summer time (UTC+2): 08:01 local is 06:01 UTC.
        (datetime(2026, 3, 30, 7, 59), True),
        (datetime(2026, 3, 30, 8, 1), False),
    ],
)
def test_out_of_hours_window(local, expected):
    moment = local.replace(tzinfo=MADRID).astimezone(UTC)
    assert auto_reply.is_out_of_hours(moment) is expected


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"received": DAY}, "office_hours"),
        ({"label": "guest_service"}, "not_demand"),
        ({"label": "non_demand"}, "not_demand"),
        ({"confidence": "medium"}, "low_confidence"),
        ({"kind": "booking"}, "booking"),
        ({"references": "<prior@example.test>"}, "thread_headers"),
        ({"subject": "RE: Disponibilidad"}, "reply_or_forward_subject"),
        ({"subject": "RV: Consulta cliente"}, "reply_or_forward_subject"),
        ({"subject": "Fwd: info"}, "reply_or_forward_subject"),
        (
            {"body": "Gracias.\n\nEl lun, 9 nov 2026, Reservas escribió:\n> Hola"},
            "quoted_history",
        ),
        (
            {"body": "Mirad esto\nDe: Juan <juan@example.test>\nEnviado: lunes"},
            "quoted_history",
        ),
        ({"sender": "Colega <pepe@albirconfort.es>"}, "internal_sender"),
        ({"sender": "noreply@example.test"}, "role_sender"),
        ({"email_date": NIGHT - timedelta(days=3)}, "old_email"),
    ],
)
def test_not_eligible(db, overrides, reason):
    _, message = add_email(db, **overrides)
    decision = decide(db, message)
    assert not decision.eligible
    assert decision.reason == reason


def test_processing_too_late_is_skipped(db):
    _, message = add_email(db)
    assert decide(db, message, at=NIGHT + timedelta(hours=3)).reason == "processed_too_late"


def test_second_message_of_a_conversation_is_skipped(db):
    conv, _ = add_email(db)
    _, second = add_email(db, conversation=conv, received=NIGHT + timedelta(minutes=5))
    assert decide(db, second).reason == "existing_conversation"


def test_disabled_mode(db, auto_reply_settings):
    auto_reply_settings.auto_reply_mode = "off"
    _, message = add_email(db)
    assert decide(db, message).reason == "disabled"
    asyncio.run(auto_reply.run(db, message.id))
    assert db.query(AutoReply).count() == 0


def test_shadow_records_without_sending(db, monkeypatch):
    monkeypatch.setattr("app.auto_reply.datetime", _frozen(NIGHT + timedelta(minutes=2)))
    monkeypatch.setattr(
        "app.integrations._deliver",
        lambda message: (_ for _ in ()).throw(AssertionError("shadow must not send")),
    )
    _, message = add_email(db, language="en")

    asyncio.run(auto_reply.run(db, message.id))
    asyncio.run(auto_reply.run(db, message.id))  # A retried job does nothing.

    reply = db.scalar(select(AutoReply))
    assert db.query(AutoReply).count() == 1
    assert reply.mode == "shadow"
    assert reply.recipient == "ana@example.test"
    assert reply.language == "en"
    assert reply.subject == "Re: Disponibilidad en diciembre"
    assert reply.text.startswith("Hello")
    assert db.query(Message).count() == 1  # Nothing outbound recorded.
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "auto_reply.shadow"))


def test_one_reply_per_address_per_day(db, monkeypatch):
    monkeypatch.setattr("app.auto_reply.datetime", _frozen(NIGHT + timedelta(minutes=2)))
    _, first = add_email(db)
    asyncio.run(auto_reply.run(db, first.id))
    _, second = add_email(db, subject="Otra consulta")
    assert decide(db, second).reason == "recently_answered"
    asyncio.run(auto_reply.run(db, second.id))
    assert db.query(AutoReply).count() == 1
    skipped = db.scalar(select(AuditEvent).where(AuditEvent.action == "auto_reply.skipped"))
    assert skipped.detail["reason"] == "recently_answered"


def test_send_mode_delivers_marked_auto_reply(db, monkeypatch, auto_reply_settings):
    auto_reply_settings.auto_reply_mode = "send"
    auto_reply_settings.auto_reply_signature = "Albir Confort"
    monkeypatch.setattr("app.auto_reply.datetime", _frozen(NIGHT + timedelta(minutes=2)))
    delivered = []
    monkeypatch.setattr("app.integrations._deliver", delivered.append)
    conv, message = add_email(db)

    asyncio.run(auto_reply.run(db, message.id))
    asyncio.run(auto_reply.run(db, message.id))

    assert len(delivered) == 1
    email = delivered[0]
    assert email["To"] == "ana@example.test"
    assert email["Auto-Submitted"] == "auto-replied"
    assert email["In-Reply-To"] == message.provider_id
    assert email["Subject"] == "Re: Disponibilidad en diciembre"
    assert email.get_content().rstrip().endswith("Albir Confort")
    reply = db.scalar(select(AutoReply))
    assert reply.mode == "sent"
    outbound = db.scalar(select(Message).where(Message.direction == "outbound"))
    assert outbound.provider_id == reply.provider_id
    assert db.get(Conversation, conv.id).status == "pending"  # The operator still answers.


def test_send_failure_is_recorded_once(db, monkeypatch, auto_reply_settings):
    auto_reply_settings.auto_reply_mode = "send"
    monkeypatch.setattr("app.auto_reply.datetime", _frozen(NIGHT + timedelta(minutes=2)))
    calls = []

    def broken(message):
        calls.append(message)
        raise OSError("smtp down")

    monkeypatch.setattr("app.integrations._deliver", broken)
    _, message = add_email(db)
    asyncio.run(auto_reply.run(db, message.id))
    asyncio.run(auto_reply.run(db, message.id))
    reply = db.scalar(select(AutoReply))
    assert reply.mode == "failed"
    assert "smtp down" in reply.error
    assert len(calls) == 1


def test_read_only_mode_still_blocks_drafts(db):
    with pytest.raises(RuntimeError):
        asyncio.run(send_approved(db, 1))


def test_analyze_enqueues_auto_reply_job_for_demand(db, monkeypatch):
    _, message = add_email(db, label=None, confidence=None)
    message.classification_data = None
    db.commit()

    async def fake_ollama(system, user, **kwargs):
        if system == CLASSIFY_SYSTEM:
            return {"classification": "demand", "confidence": "high", "reason": "Consulta."}
        if system == EXTRACT_SYSTEM:
            return {"customer_language": "fr"}
        return {"text": "Borrador de prueba para el operador."}

    monkeypatch.setattr("app.processing.ollama_json", fake_ollama)
    asyncio.run(analyze_message(db, message.id))
    job = db.scalar(select(Job).where(Job.kind == "auto_reply"))
    assert job.payload == {"message_id": message.id}
    assert auto_reply.language(message) == "fr"


def test_email_date_header_is_parsed():
    message = EmailMessage()
    message["Date"] = "Wed, 11 Nov 2026 23:30:00 +0100"
    assert _email_date(message) == datetime(2026, 11, 11, 22, 30, tzinfo=UTC)
    broken = EmailMessage()
    broken["Date"] = "not a date"
    assert _email_date(broken) is None
    assert _email_date(EmailMessage()) is None


def _frozen(moment: datetime):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment if tz else moment.replace(tzinfo=None)

    return Frozen
