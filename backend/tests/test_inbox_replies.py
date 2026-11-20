"""Pending conversations answered from the mailbox. Addresses are fictitious."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import inbox_replies, notifications
from app.config import get_settings
from app.inbox_replies import answer_in, check_inbox_replies, outside_reply
from app.ingest import ingest
from app.models import AuditEvent, Conversation, Message, Notification
from tests.test_staff_replies import FakeImap, sent


@pytest.fixture(autouse=True)
def _fresh_cursor():
    inbox_replies._cursor["after_id"] = 0
    yield


WROTE = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
CUSTOMER = "liv@example.test"
FIRST_ID = "<first@example.test>"


def pending(db, *, address=f"Liv <{CUSTOMER}>", provider_id=FIRST_ID, kind="inquiry",
            wrote=WROTE):
    conversation = Conversation(channel="email", external_key=provider_id, kind=kind,
                                customer_address=address, status="pending",
                                last_inbound_at=wrote)
    db.add(conversation)
    db.flush()
    db.add(Message(conversation_id=conversation.id, channel="email", provider_id=provider_id,
                   direction="inbound", body="Apartment in March?", sender=address,
                   email_date=wrote, created_at=wrote))
    db.commit()
    return conversation


@contextmanager
def configured(monkeypatch, imap):
    settings = get_settings()
    monkeypatch.setattr(settings, "imap_host", "imap.example.test")
    monkeypatch.setattr(settings, "imap_user", "reservas")
    monkeypatch.setattr(settings, "internal_sender_domains", "albirconfort.test")
    monkeypatch.setattr(settings, "booking_sender_domains", "booking.com")

    @contextmanager
    def folder():
        yield imap

    monkeypatch.setattr(inbox_replies, "open_sent_folder", folder)
    yield


def found(sent_at, references):
    return {"sent_at": sent_at, "references": references, "message_id": "<r@x>",
            "subject": "RE", "sender": "Reservas"}


def test_answer_must_be_in_the_thread_and_after_the_last_customer_message():
    ids = {FIRST_ID}
    other_thread = found(WROTE + timedelta(hours=2), "<other@x>")
    in_thread = found(WROTE + timedelta(hours=1), FIRST_ID)
    assert answer_in([other_thread, in_thread], ids, WROTE) is in_thread
    # An answer to an earlier message does not answer what the customer wrote afterwards.
    assert answer_in([found(WROTE - timedelta(hours=3), FIRST_ID)], ids, WROTE) is None
    assert answer_in([found(WROTE + timedelta(hours=1), "<other@x>")], ids, WROTE) is None


def test_worker_marks_answered_conversations_and_clears_the_overdue_alert(db, monkeypatch):
    answered = pending(db)
    waiting = pending(db, address="Ole <ole@example.test>", provider_id="<ole@x>")
    colleague = pending(db, address="Ana <ana@albirconfort.test>", provider_id="<ana@x>")
    booking = pending(db, address="Bo <bo@example.test>", provider_id="<bo@x>", kind="booking")
    imap = FakeImap({
        3: sent(CUSTOMER, "Wed, 18 Nov 2026 11:20:00 +0100", "RE: Apartment",
                id="<reply@x>", in_reply_to=FIRST_ID, references=FIRST_ID),
        4: sent("ole@example.test", "Wed, 18 Nov 2026 11:20:00 +0100", "Newsletter"),
        5: sent("bo@example.test", "Wed, 18 Nov 2026 11:20:00 +0100", "RE",
                in_reply_to="<bo@x>"),
    })
    current = WROTE + timedelta(hours=5)
    notifications.scan(db, current)
    assert db.scalar(select(Notification).where(Notification.kind == "inbox.overdue"))

    with configured(monkeypatch, imap):
        assert check_inbox_replies(db, current) == 1
    assert answered.status == "answered"
    assert waiting.status == colleague.status == booking.status == "pending"
    assert outside_reply(db, answered) == {
        "sent_at": "2026-11-18T10:20:00+00:00", "subject": "RE: Apartment",
        "sender": None,
    }
    assert outside_reply(db, waiting) is None
    # Read-only towards the mailbox.
    assert {command[0] for command in imap.commands} == {"SEARCH", "FETCH"}
    assert all("BODY.PEEK" in command[2] for command in imap.commands if command[0] == "FETCH")

    notifications.scan(db, current)
    overdue = db.scalar(select(Notification).where(Notification.kind == "inbox.overdue",
                                                   Notification.resolved_at.is_(None)))
    assert f"#{answered.id}" not in overdue.body and f"#{waiting.id}" in overdue.body


def test_same_reply_never_marks_again_after_an_operator_puts_it_back(db, monkeypatch):
    conversation = pending(db)
    imap = FakeImap({
        3: sent(CUSTOMER, "Wed, 18 Nov 2026 11:20:00 +0100", "RE: Apartment",
                id="<reply@x>", in_reply_to=FIRST_ID),
    })
    with configured(monkeypatch, imap):
        assert check_inbox_replies(db, WROTE + timedelta(hours=5)) == 1
        conversation.status = "pending"  # the operator says it still needs an answer
        db.commit()
        assert check_inbox_replies(db, WROTE + timedelta(hours=6)) == 0
        assert conversation.status == "pending"
        imap.messages[4] = sent(CUSTOMER, "Wed, 18 Nov 2026 16:00:00 +0100", "RE: RE: Apartment",
                                id="<reply-2@x>", references=f"{FIRST_ID} <reply@x>")
        assert check_inbox_replies(db, WROTE + timedelta(hours=8)) == 1
    assert conversation.status == "answered"
    events = db.scalars(select(AuditEvent).where(
        AuditEvent.action == "conversation.answered_outside")).all()
    assert [event.detail["message_id"] for event in events] == ["<reply@x>", "<reply-2@x>"]


def test_a_new_customer_message_puts_it_back_to_pending(db, monkeypatch):
    conversation = pending(db)
    imap = FakeImap({
        3: sent(CUSTOMER, "Wed, 18 Nov 2026 11:20:00 +0100", "RE: Apartment",
                id="<reply@x>", in_reply_to=FIRST_ID),
    })
    with configured(monkeypatch, imap):
        check_inbox_replies(db, WROTE + timedelta(hours=5))
    assert conversation.status == "answered"
    ingest(db, channel="email", external_key=FIRST_ID, provider_id="<again@x>",
           body="And April?", sender=f"Liv <{CUSTOMER}>",
           email_date=WROTE + timedelta(days=1))
    db.commit()
    assert conversation.status == "pending"
    assert outside_reply(db, conversation) is None
    with configured(monkeypatch, imap):  # the old reply does not answer the new message
        assert check_inbox_replies(db, WROTE + timedelta(days=1, hours=1)) == 0


def test_each_run_continues_where_the_last_one_stopped_and_skips_platforms(db, monkeypatch):
    monkeypatch.setattr(inbox_replies, "MAX_PER_RUN", 2)
    platform = pending(db, address="Avantio <noreply@booking.com>", provider_id="<p@x>")
    customers = [
        pending(db, address=f"C{n} <c{n}@example.test>", provider_id=f"<c{n}@x>")
        for n in range(5)
    ]
    imap = FakeImap({})
    with configured(monkeypatch, imap):
        seen = []
        for _ in range(3):
            seen.append([item.id for item, _ in inbox_replies.candidates(db, WROTE)])
    ids = [item.id for item in customers]
    assert seen == [ids[0:2], ids[2:4], [ids[4], ids[0]]]
    assert platform.id not in sum(seen, [])


def notice(db, *, address="Hotelbeds <noreply@hotelbeds.com>", label="non_demand",
           confidence="high", override=None, provider_id=None):
    _counter = db.query(Conversation).count() + 1
    provider_id = provider_id or f"<notice{_counter}@x>"
    conversation = pending(db, address=address, provider_id=provider_id)
    message = db.scalar(select(Message).where(Message.conversation_id == conversation.id))
    message.classification_label = label
    message.classification_confidence = confidence
    message.classification_override = override
    db.commit()
    return conversation


def test_confident_automatic_notices_are_filed_and_the_rest_stays_pending(db, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "internal_sender_domains", "albirconfort.test")
    monkeypatch.setattr(settings, "booking_sender_domains", "booking.com")
    price_update = notice(db)
    unsure = notice(db, confidence="medium")
    guest_message = notice(db, address="Avantio <noreply@avantio.com>", label="guest_service")
    unclassified = notice(db, label=None, confidence=None)
    person_said = notice(db, label="uncertain", confidence="low", override="non_demand")
    customer = notice(db, address=f"Liv <{CUSTOMER}>")  # a person, even if not demand
    colleague = notice(db, address="Ana <ana@albirconfort.test>")

    assert inbox_replies.file_platform_notices(db, WROTE) == 2
    assert price_update.status == person_said.status == "filed"
    for item in (unsure, guest_message, unclassified, customer, colleague):
        assert item.status == "pending"
    assert inbox_replies.filed_notice(db, price_update)["reason"] == "aviso_automatico"
    assert inbox_replies.filed_notice(db, customer) is None


def test_filed_notice_stays_pending_once_an_operator_reopens_it(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "internal_sender_domains", "albirconfort.test")
    conversation = notice(db, provider_id="<price@x>")
    inbox_replies.file_platform_notices(db, WROTE)
    conversation.status = "pending"
    db.commit()
    assert inbox_replies.file_platform_notices(db, WROTE) == 0
    assert conversation.status == "pending"
    # A new notice in the thread files it again once it is classified.
    ingest(db, channel="email", external_key="<price@x>", provider_id="<price-2@x>",
           body="Bulk price change finished", sender="Hotelbeds <noreply@hotelbeds.com>")
    db.commit()
    assert inbox_replies.file_platform_notices(db, WROTE) == 0  # not classified yet
    newest = db.scalar(select(Message).where(Message.provider_id == "<price-2@x>"))
    newest.classification_label, newest.classification_confidence = "non_demand", "high"
    db.commit()
    assert inbox_replies.file_platform_notices(db, WROTE) == 1
    assert conversation.status == "filed"


def test_notices_that_may_ask_for_something_are_never_filed_and_reopen(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "internal_sender_domains", "albirconfort.test")
    price = notice(db)
    actionable = {}
    for subject in ("Tienes un nuevo mensaje", "Reserva: alojamiento AVDA. BLQ.A - 2ºB",
                    "Detalle de comunicaciones erróneas del lote 2", "Re: cierres 2027",
                    "Check-in online realizado de la reserva"):
        item = notice(db, address="Avantio <noreply@avantio.com>")
        item.subject = subject
        actionable[subject] = item
    db.commit()
    assert inbox_replies.file_platform_notices(db, WROTE) == 1
    assert price.status == "filed"
    assert {item.status for item in actionable.values()} == {"pending"}

    # Filed by the earlier rule: the current one puts it back to pending.
    message_notice = actionable["Tienes un nuevo mensaje"]
    message_notice.status = "filed"
    db.commit()
    assert inbox_replies.reopen_actionable_notices(db) == 1
    assert message_notice.status == "pending"
    assert price.status == "filed"
    assert db.scalar(select(AuditEvent).where(
        AuditEvent.action == "conversation.notice_reopened",
        AuditEvent.conversation_id == message_notice.id))
