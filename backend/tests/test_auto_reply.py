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
    monkeypatch.setattr(settings, "imap_host", "imap.example.test")
    monkeypatch.setattr(settings, "smtp_from_name", "Albir Confort Holidays")
    monkeypatch.setattr(settings, "auto_reply_banner", True)
    monkeypatch.setattr(settings, "auto_reply_banner_url", "https://albirconfort-holidays.com/")
    monkeypatch.setattr(settings, "auto_reply_sent_folder", "")
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
    assert email["From"] == "Albir Confort Holidays <reservas@albirconfort.es>"
    assert email["Date"]
    assert email.get_body(("plain",)).get_content().rstrip().endswith("Albir Confort")
    html_part = email.get_body(("html",))
    html_text = html_part.get_content()
    assert '<a href="https://albirconfort-holidays.com/"><img src="cid:' in html_text
    images = [part for part in email.walk() if part.get_content_type() == "image/png"]
    assert len(images) == 1
    assert images[0].get_payload(decode=True) == auto_reply.BANNER.read_bytes()
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


def test_simulation_replays_history_without_writing(db, auto_reply_settings):
    auto_reply_settings.auto_reply_mode = "off"  # The simulation ignores the switch.
    conv, first = add_email(db)  # Wednesday night: answered.
    # The customer's later reply in the same thread does not disqualify the first email.
    add_email(
        db, conversation=conv, subject="RE: Disponibilidad", references=first.provider_id,
        received=NIGHT + timedelta(hours=5),
    )
    add_email(db, subject="Otra pregunta", received=NIGHT + timedelta(hours=1))  # Same day.
    add_email(db, sender="luc@example.test", received=DAY, language="fr")  # Office hours.
    add_email(db, sender="luc@example.test", received=NIGHT + timedelta(days=3), language="fr")

    result = auto_reply.simulate(
        db, start=NIGHT - timedelta(days=1), end=NIGHT + timedelta(days=5)
    )

    assert result["total"] == 5
    assert result["replies"] == 2
    assert [item["message_id"] for item in result["items"]][0] == first.id
    assert result["items"][1]["recipient"] == "luc@example.test"
    assert result["items"][1]["language"] == "fr"
    assert result["reasons"]["recently_answered"] == 1
    assert result["reasons"]["office_hours"] == 1
    assert result["reasons"]["thread_headers"] == 1
    assert db.query(AutoReply).count() == 0
    assert db.query(AuditEvent).count() == 0


def test_operator_corrections_win_over_the_model(db):
    _, confirmed = add_email(db, confidence="low")
    confirmed.classification_override = "demand"
    _, corrected = add_email(db, sender="otra@example.test")
    corrected.classification_override = "non_demand"
    db.commit()
    assert decide(db, confirmed).eligible
    assert decide(db, corrected).reason == "not_demand"


def test_simulation_can_ignore_office_hours(db):
    add_email(db, received=DAY)
    window = {"start": DAY - timedelta(hours=1), "end": DAY + timedelta(hours=1)}
    assert auto_reply.simulate(db, **window)["replies"] == 0
    assert auto_reply.simulate(db, **window, any_hour=True)["replies"] == 1


def test_sent_copy_goes_to_configured_folder(db, monkeypatch, auto_reply_settings):
    auto_reply_settings.auto_reply_mode = "send"
    auto_reply_settings.auto_reply_sent_folder = "Elementos enviados"
    monkeypatch.setattr("app.auto_reply.datetime", _frozen(NIGHT + timedelta(minutes=2)))
    delivered, copies = [], []
    monkeypatch.setattr("app.integrations._deliver", delivered.append)
    monkeypatch.setattr("app.mail.append_sent_copy", lambda m, f: copies.append((m, f)))
    _, message = add_email(db)
    asyncio.run(auto_reply.run(db, message.id))
    assert copies == [(delivered[0], "Elementos enviados")]


def test_failed_copy_does_not_undo_the_reply(db, monkeypatch, auto_reply_settings):
    auto_reply_settings.auto_reply_mode = "send"
    auto_reply_settings.auto_reply_sent_folder = "Elementos enviados"
    monkeypatch.setattr("app.auto_reply.datetime", _frozen(NIGHT + timedelta(minutes=2)))
    monkeypatch.setattr("app.integrations._deliver", lambda m: None)

    def broken(message, folder):
        raise OSError("imap down")

    monkeypatch.setattr("app.mail.append_sent_copy", broken)
    _, message = add_email(db)
    asyncio.run(auto_reply.run(db, message.id))
    assert db.scalar(select(AutoReply)).mode == "sent"
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "auto_reply.copy_failed"))


def test_no_copy_without_folder(db, monkeypatch, auto_reply_settings):
    auto_reply_settings.auto_reply_mode = "send"
    monkeypatch.setattr("app.auto_reply.datetime", _frozen(NIGHT + timedelta(minutes=2)))
    monkeypatch.setattr("app.integrations._deliver", lambda m: None)
    monkeypatch.setattr(
        "app.mail.append_sent_copy",
        lambda m, f: (_ for _ in ()).throw(AssertionError("no folder configured")),
    )
    _, message = add_email(db)
    asyncio.run(auto_reply.run(db, message.id))
    assert db.scalar(select(AutoReply)).mode == "sent"


def test_append_sent_copy_uses_imap_append(monkeypatch, auto_reply_settings):
    from app.mail import append_sent_copy

    calls = []

    class FakeImap:
        def __init__(self, *args, **kwargs):
            pass

        def login(self, user, password):
            calls.append("login")

        def append(self, mailbox, flags, date_time, raw):
            calls.append((mailbox, flags, raw))
            return "OK", [b"APPEND completed"]

        def logout(self):
            calls.append("logout")

    monkeypatch.setattr("app.mail.imaplib.IMAP4_SSL", FakeImap)
    message = EmailMessage()
    message["Subject"] = "Copia"
    message.set_content("hola")
    append_sent_copy(message, "Elementos enviados")
    assert calls[1] == ('"Elementos enviados"', r"(\Seen)", message.as_bytes())
    assert calls[-1] == "logout"


def test_from_header_variants(auto_reply_settings):
    from app.integrations import from_header

    assert from_header() == "Albir Confort Holidays <reservas@albirconfort.es>"
    auto_reply_settings.smtp_from_name = ""
    assert from_header() == "reservas@albirconfort.es"
    auto_reply_settings.smtp_from = "Albir <reservas@albirconfort.es>"
    assert from_header() == "Albir <reservas@albirconfort.es>"


@pytest.mark.parametrize(
    ("sender", "expected"),
    [
        # The address confirms the name.
        ("ANA GARCÍA <ana.garcia@example.test>", "Ana"),
        ("José Pérez <jperez@example.test>", "José"),
        ("'Dag Tessem' <dtessem@online.no>", "Dag"),
        ('"Tessem, Dag" <dtessem@online.no>', "Dag"),
        ("Helen Rooney <hrooney571@gmail.com>", "Helen"),
        ("Jean-Luc Picard <jean-luc.picard@example.test>", "Jean-Luc"),
        # Not clear: no name, a company, a nickname or an address that says otherwise.
        ("Katarzyna Szymczak <kasia80.szymczak@gmail.com>", "Katarzyna"),
        ("Sølvi Sommervoll <solvisom@ntebb.no>", "Sølvi"),
        ("Ana <ana@example.test>", None),
        ("Aisling Killeen Dickson <simondickson@icloud.com>", None),
        ("Extranet Sidetours <extranet@sidetours.com>", None),
        ("Product Muchosol <product@muchosol.com>", None),
        ("Gmail KARBAR Grup <karbarsolucionesindustriales@gmail.com>", None),
        ("Vrbo Integrations <integrationsupport@expediagroup.com>", None),
        ("Ana Garcia <contacto@example.test>", None),
        ("Viajes Sol <info@viajessol.com>", None),
        ("Mibtrysil <mibtrysil@gmail.com>", None),
        ("Familia Garcia <familiagarcia@example.test>", None),
        ("\"'ana.smc ana.smc'\" <ana.smc@telefonica.net>", None),
        ("Info <info@example.test>", None),
        ("Reservas Hotel Sol <reservas@hotelsol.es>", None),
        ("ana@example.test", None),
        ("hrooney571@gmail.com <hrooney571@gmail.com>", None),
        ("J <j@example.test>", None),
        ("Ana Maria de la Cruz Lopez <ana@example.test>", None),
    ],
)
def test_first_name(sender, expected):
    assert auto_reply.first_name(Message(sender=sender)) == expected


@pytest.mark.parametrize(
    ("local", "lang", "expected"),
    [
        (datetime(2026, 11, 11, 23, 30), "es", "Mañana a partir de las 8:00"),
        (datetime(2026, 11, 12, 3, 0), "es", "Hoy a partir de las 8:00"),
        (datetime(2026, 11, 13, 21, 0), "es", "El lunes a partir de las 8:00"),
        (datetime(2026, 11, 14, 12, 0), "en", "On Monday from 8:00 (Spanish time)"),
        (datetime(2026, 11, 15, 22, 0), "de", "Morgen ab 8:00 Uhr (spanische Zeit)"),
        (datetime(2026, 11, 13, 21, 0), "fr", "Lundi à partir de 8h (heure espagnole)"),
        (datetime(2026, 11, 13, 21, 0), "pt", "Na segunda-feira a partir das 8:00"),
    ],
)
def test_reply_says_when_the_team_answers(local, lang, expected):
    received = local.replace(tzinfo=MADRID).astimezone(UTC)
    assert expected in auto_reply.compose(lang, received=received)


@pytest.mark.parametrize(
    ("data", "sender", "expected"),
    [
        ({"language": "nb", "language_detected": True}, "x@online.no", "nb"),
        ({"language": "de", "language_detected": True}, "x@example.test", "de"),
        ({"language": "other", "language_detected": True}, "x@example.nl", "en"),
        # The model gave no language: Spanish only for a Spanish address.
        ({"language": "es", "language_detected": False}, "x@online.no", "en"),
        ({"language": "es", "language_detected": False}, "x@telefonica.es", "es"),
        (None, "x@gmail.com", "en"),
        # Data stored before language_detected existed keeps the detected language.
        ({"language": "es"}, "x@gmail.com", "es"),
    ],
)
def test_language_choice(data, sender, expected):
    assert auto_reply.language(Message(sender=sender, classification_data=data)) == expected


def test_extraction_marks_whether_language_was_detected():
    from app.processing import _safe_analysis

    assert _safe_analysis({"customer_language": "nb"})["language"] == "nb"
    assert _safe_analysis({"customer_language": "nb"})["language_detected"] is True
    missing = _safe_analysis({})
    assert missing["language"] == "es"  # Drafts keep their Spanish default.
    assert missing["language_detected"] is False


def test_norwegian_reply(auto_reply_settings):
    received = datetime(2026, 11, 13, 21, 0, tzinfo=MADRID).astimezone(UTC)
    text = auto_reply.compose("nb", name="Dag", received=received)
    assert text.startswith("Hei Dag,")
    assert "Mandag fra kl. 08.00 vil en av oss" in text
    assert text.rstrip().endswith("Reservasjonsteamet i Albir Confort")


def test_message_is_personal_and_not_marked_automatic(auto_reply_settings):
    text = auto_reply.compose("es", name="Ana", received=NIGHT)
    assert text.startswith("Hola Ana,\n\nMuchas gracias por escribirnos")
    assert "albirconfort-holidays.com" in text
    assert text.rstrip().endswith("Equipo de reservas de Albir Confort")
    for lang in auto_reply.TEMPLATES:
        assert "autom" not in auto_reply.compose(lang, received=NIGHT).lower()
    assert auto_reply.compose("other", received=NIGHT).startswith("Hola,")


def test_banner_can_be_disabled(auto_reply_settings):
    assert "<img" in auto_reply.compose_html("Hola")
    auto_reply_settings.auto_reply_banner = False
    assert "<img" not in auto_reply.compose_html("Hola")
