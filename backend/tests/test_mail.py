from __future__ import annotations

from email.message import EmailMessage

from sqlalchemy import select

from app.config import get_settings
from app.mail import poll_mail
from app.models import Conversation, Message


def test_imap_read_only_booking_and_duplicate(db, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "imap_host", "imap.example.test")
    monkeypatch.setattr(settings, "imap_user", "team@example.test")
    monkeypatch.setattr(settings, "imap_password", "test-password")
    monkeypatch.setattr(settings, "booking_sender_domains", "booking.com")
    monkeypatch.setattr(settings, "imap_initial_backfill", 0)
    message = EmailMessage()
    message["From"] = "Booking <notifications@booking.com>"
    message["To"] = "team@example.test"
    message["Subject"] = "Prerreserva apartamento A"
    message["Message-ID"] = "<booking-123@example.test>"
    message.set_content("Llegada 10/11/2026, salida 13/11/2026")
    calls = []

    class FakeImap:
        def __init__(self, *args, **kwargs):
            pass

        def login(self, user, password):
            calls.append("login")

        def select(self, mailbox, readonly=False):
            calls.append(("select", readonly))
            return "OK", [b"1"]

        def status(self, mailbox, fields):
            return "OK", [b"INBOX (UIDVALIDITY 1 UIDNEXT 1)"]

        def uid(self, action, *args):
            calls.append((action, args))
            if action == "SEARCH":
                return "OK", [b"1"]
            if action == "FETCH":
                return "OK", [(b"1 (BODY[])", message.as_bytes())]
            raise AssertionError(f"Unexpected IMAP action {action}")

        def close(self):
            pass

        def logout(self):
            pass

    monkeypatch.setattr("app.mail.imaplib.IMAP4_SSL", FakeImap)
    assert poll_mail(db) == 0  # Initial watermark, no historical import.
    assert poll_mail(db) == 1
    assert poll_mail(db) == 0
    conv = db.scalar(select(Conversation))
    assert conv.kind == "booking"
    assert conv.priority == "high"
    assert db.query(Message).count() == 1
    assert ("select", True) in calls
    assert any(
        item[0] == "FETCH" and item[1][1] == "(BODY.PEEK[])"
        for item in calls
        if isinstance(item, tuple)
    )
