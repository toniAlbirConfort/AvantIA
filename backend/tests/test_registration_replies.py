"""Second check of unregistered consultations against the mailbox's Sent folder.

Names and addresses are fictitious."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app import backfill_email_dates, notifications, registration_replies
from app.config import get_settings
from app.models import Conversation, Message, Notification, RegistrationCheck
from app.registration import check_view, office_deadline
from app.registration_replies import (
    accepted,
    apply,
    check_registration_replies,
    parse_suggestion,
    staff_reply_view,
    suggest_registration_reasons,
)
from tests.test_staff_replies import FakeImap, sent

RECEIVED = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
CUSTOMER = "nora@example.test"
CUSTOMER_ID = "<consulta-1@example.test>"


def alerted_check(db, *, email=CUSTOMER, provider_id=CUSTOMER_ID, status="alerted"):
    conversation = Conversation(channel="email", external_key=provider_id, subject="Apartment")
    db.add(conversation)
    db.flush()
    message = Message(
        conversation_id=conversation.id,
        channel="email",
        provider_id=provider_id,
        direction="inbound",
        sender=f"Nora <{email}>",
        subject="Apartment 20 Feb - 12 Mar",
        body="Do you have an apartment from 20 February to 12 March?",
        email_date=RECEIVED,
        created_at=RECEIVED,
    )
    db.add(message)
    db.flush()
    check = RegistrationCheck(
        conversation_id=conversation.id,
        message_id=message.id,
        customer_email=email,
        received_at=RECEIVED,
        due_at=RECEIVED + timedelta(hours=1),
        status=status,
        mode="shadow",
        alerted_at=RECEIVED + timedelta(hours=2) if status == "alerted" else None,
    )
    db.add(check)
    db.commit()
    return check, message


def reply(sent_at, references=""):
    return {"uid": b"7", "sent_at": sent_at, "subject": "RE: Apartment",
            "sender": "Albir Confort <reservas@example.test>", "references": references}


def test_a_reply_in_the_thread_counts_even_if_the_consultation_was_stored_late(db):
    check, message = alerted_check(db)
    earlier = RECEIVED - timedelta(days=7)  # the first inbox load stored it a week late
    assert accepted(check, message, reply(earlier, CUSTOMER_ID))["in_thread"] is True
    # Outside the thread, an older email says nothing about this consultation.
    assert accepted(check, message, reply(earlier, "<other@x>")) is None
    later = accepted(check, message, reply(RECEIVED + timedelta(hours=3), "<other@x>"))
    assert later["in_thread"] is False
    assert accepted(check, message, None) is None


def test_a_new_reply_drops_the_previous_suggestion(db):
    check, message = alerted_check(db)
    at = RECEIVED + timedelta(days=1)
    assert apply(check, {**reply(RECEIVED + timedelta(hours=2)), "in_thread": True}, at) is True
    check.suggestion, check.suggestion_reason = "sin_disponibilidad", "No quedan fechas"
    # Same reply again: nothing changes.
    assert apply(check, {**reply(RECEIVED + timedelta(hours=2)), "in_thread": True}, at) is False
    assert check.suggestion == "sin_disponibilidad"
    assert apply(check, {**reply(RECEIVED + timedelta(hours=5)), "in_thread": True}, at) is True
    assert check.suggestion is None and check.suggestion_reason is None


def test_view_says_whether_the_team_answered_and_what_the_model_suggests(db):
    check, _ = alerted_check(db)
    assert check_view(check)["staff_reply"] is None  # not looked up yet
    apply(check, None, RECEIVED + timedelta(days=1))
    view = staff_reply_view(check)
    assert view["sent_at"] is None and view["checked_at"] is not None
    apply(check, {**reply(RECEIVED + timedelta(hours=2)), "in_thread": True},
          RECEIVED + timedelta(days=1))
    check.suggestion = "huesped_existente"
    view = check_view(check)["staff_reply"]
    assert view["in_thread"] is True
    assert view["sender"] == "Albir Confort <reservas@example.test>"
    assert view["suggested_dismiss"] == "huesped_existente"
    check.suggestion = "sin_disponibilidad"  # still has to be registered
    assert staff_reply_view(check)["suggested_dismiss"] is None


def test_worker_looks_up_open_checks_only_and_reads_without_changing_the_mailbox(
    db, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "imap_host", "imap.example.test")
    monkeypatch.setattr(settings, "imap_user", "reservas")
    monkeypatch.setattr(settings, "registration_alert_lookback_days", 3650)
    answered, _ = alerted_check(db)
    nobody, _ = alerted_check(db, email="ole@example.test", provider_id="<consulta-2@x>")
    closed, _ = alerted_check(db, email="eva@example.test", provider_id="<consulta-3@x>",
                              status="registered")
    imap = FakeImap({
        5: sent(CUSTOMER, "Tue, 10 Nov 2026 12:30:00 +0100", "RE: Apartment",
                in_reply_to=CUSTOMER_ID, references=CUSTOMER_ID),
        6: sent("eva@example.test", "Tue, 10 Nov 2026 12:30:00 +0100", "RE: Booking"),
    })

    @contextmanager
    def fake_folder():
        yield imap

    monkeypatch.setattr(registration_replies, "open_sent_folder", fake_folder)
    assert check_registration_replies(db) == 2
    assert answered.staff_reply_at == datetime(2026, 11, 10, 11, 30, tzinfo=UTC)
    assert answered.staff_reply_in_thread is True
    assert nobody.staff_checked_at is not None and nobody.staff_reply_at is None
    assert closed.staff_checked_at is None
    assert {command[0] for command in imap.commands} == {"SEARCH", "FETCH"}
    assert all("BODY.PEEK" in command[2] for command in imap.commands if command[0] == "FETCH")


def open_alerts(db):
    return {
        item.dedupe_key: item
        for item in db.scalars(
            select(Notification).where(
                Notification.kind.in_(("registration.unanswered", "registration.unregistered")),
                Notification.resolved_at.is_(None),
            )
        ).all()
    }


def test_alert_splits_into_unanswered_and_answered_and_clears_when_registered(
    db, monkeypatch
):
    monkeypatch.setattr(get_settings(), "auto_reply_timezone", "Europe/Madrid")
    check, _ = alerted_check(db)
    at = RECEIVED + timedelta(days=1)

    notifications.scan(db, at)  # not looked up yet: the generic alert
    assert list(open_alerts(db)) == [f"registration.unregistered:{check.id}"]

    apply(check, None, at)
    db.commit()
    notifications.scan(db, at)
    alerts = open_alerts(db)
    assert list(alerts) == [f"registration.unanswered:{check.id}"]
    assert alerts[f"registration.unanswered:{check.id}"].title == (
        f"Consulta #{check.conversation_id} sin responder ni registrar"
    )

    apply(check, {**reply(RECEIVED + timedelta(hours=3)), "in_thread": True}, at)
    db.commit()
    notifications.scan(db, at)
    notifications.scan(db, at)  # no duplicates
    alerts = open_alerts(db)
    assert list(alerts) == [f"registration.unregistered:{check.id}"]
    assert "El equipo respondió el 10/11 13:00" in alerts[
        f"registration.unregistered:{check.id}"
    ].body

    check.status = "registered"
    db.commit()
    notifications.scan(db, at)
    assert open_alerts(db) == {}


def test_parse_suggestion_accepts_only_known_categories():
    assert parse_suggestion({"categoria": "Oferta", "motivo": "  Envió  dos pisos "}) == (
        "oferta", "Envió dos pisos"
    )
    assert parse_suggestion({"categoria": "registrar_ya", "motivo": "x"}) is None
    assert parse_suggestion({}) is None


def test_suggestion_is_a_hint_that_never_resolves_the_check(db, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ollama_url", "http://ollama.test")
    monkeypatch.setattr(settings, "ollama_model", "test")
    monkeypatch.setattr(settings, "imap_host", "imap.example.test")
    monkeypatch.setattr(settings, "imap_user", "reservas")
    check, _ = alerted_check(db)
    apply(check, {**reply(RECEIVED + timedelta(hours=3)), "in_thread": True}, RECEIVED)
    db.commit()
    seen = {}

    async def fake_ollama(system, user, **_):
        seen["user"] = user
        return {"categoria": "huesped_existente", "motivo": "Ya tiene la reserva 1234"}

    monkeypatch.setattr(registration_replies, "reply_message",
                        lambda check, message: {"body": "Your booking is confirmed."})
    monkeypatch.setattr(registration_replies, "ollama_json", fake_ollama)
    assert asyncio.run(suggest_registration_reasons(db)) == 1
    assert check.suggestion == "huesped_existente"
    assert check.status == "alerted" and check.resolved_at is None
    assert "Your booking is confirmed." in seen["user"]
    # Done once: it is not asked again for the same reply.
    assert asyncio.run(suggest_registration_reasons(db)) == 0


def test_failed_suggestion_waits_before_retrying(db, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ollama_url", "http://ollama.test")
    monkeypatch.setattr(settings, "ollama_model", "test")
    monkeypatch.setattr(settings, "imap_host", "imap.example.test")
    monkeypatch.setattr(settings, "imap_user", "reservas")
    check, _ = alerted_check(db)
    apply(check, {**reply(RECEIVED + timedelta(hours=3)), "in_thread": True}, RECEIVED)
    db.commit()

    async def down(*_, **__):
        raise RuntimeError("Ollama caído")

    monkeypatch.setattr(registration_replies, "reply_message",
                        lambda check, message: {"body": "Sorry, no availability."})
    monkeypatch.setattr(registration_replies, "ollama_json", down)
    assert asyncio.run(suggest_registration_reasons(db)) == 0
    assert check.suggestion is None and check.suggestion_checked_at is not None
    assert registration_replies.suggestions_due(db, datetime.now(UTC)) == []
    later = datetime.now(UTC) + registration_replies.SUGGESTION_RETRY + timedelta(minutes=1)
    assert registration_replies.suggestions_due(db, later) == [check]


class FakeInbox:
    def __init__(self, dates: dict[str, str]):
        self.dates = dates
        self.commands: list[tuple] = []

    def uid(self, command, *args):
        self.commands.append((command, *args))
        if command == "SEARCH":
            wanted = args[-1].strip('"')
            uids = [str(index + 1) for index, key in enumerate(self.dates) if key == wanted]
            return "OK", [" ".join(uids).encode()]
        if command == "FETCH":
            key = list(self.dates)[int(args[0]) - 1]
            header = f"Date: {self.dates[key]}\r\n".encode()
            return "OK", [(b"1 (BODY[HEADER.FIELDS (DATE)]", header), b")"]
        raise AssertionError(command)


def test_backfill_moves_the_deadline_to_the_real_date_and_reports_first(db, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "registration_alert_delay_minutes", 60)
    monkeypatch.setattr(settings, "auto_reply_timezone", "Europe/Madrid")
    check, message = alerted_check(db)
    message.email_date = None
    db.commit()
    inbox = FakeInbox({CUSTOMER_ID: "Tue, 3 Nov 2026 10:00:00 +0100"})

    report = backfill_email_dates.backfill(db, inbox)
    assert report == {"applied": False, "without_date": 1, "found": 1, "checks": 1}
    assert message.email_date is None and check.received_at == RECEIVED

    backfill_email_dates.backfill(db, inbox, apply=True)
    real = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
    assert message.email_date == real
    assert check.received_at == real
    assert check.due_at == office_deadline(real, 60)
    assert all("BODY.PEEK" in command[2] for command in inbox.commands if command[0] == "FETCH")


def test_backfill_skips_unsafe_message_ids(db):
    inbox = FakeInbox({})
    assert backfill_email_dates.header_date(inbox, '<x" OR ALL "@y>') is None
    assert inbox.commands == []
