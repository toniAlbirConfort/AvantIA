"""Pending conversations answered from the mailbox. Addresses are fictitious."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app import inbox_replies, notifications
from app.config import get_settings
from app.inbox_replies import answer_in, check_inbox_replies, outside_reply
from app.ingest import ingest
from app.models import AuditEvent, Conversation, Message, Notification
from tests.test_staff_replies import FakeImap, sent

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
