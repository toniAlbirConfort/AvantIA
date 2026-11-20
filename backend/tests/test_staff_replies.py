from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.automatic_messages import list_automatic_messages
from app.models import AutoReply, Conversation, Message
from app.staff_replies import apply, due, latest_sent, sent_folder, staff_reply_view

BASE = datetime(2026, 11, 16, 19, 51, tzinfo=UTC)
CUSTOMER_ID = "<customer-1@example.test>"
AUTO_ID = "<avantia-auto-1@albirconfort.test>"


class FakeImap:
    """Sent folder with (uid, headers) pairs; records every command to prove it only reads."""

    def __init__(self, messages: dict[int, dict], boxes=None):
        self.messages = messages
        self.boxes = boxes or []
        self.commands: list[tuple] = []

    def list(self):
        return "OK", self.boxes

    def uid(self, command, *args):
        self.commands.append((command, *args))
        if command == "SEARCH":
            address = args[-1].strip('"')
            found = [uid for uid, item in self.messages.items() if item["to"] == address]
            return "OK", [" ".join(str(uid) for uid in found).encode()]
        if command == "FETCH":
            item = self.messages[int(args[0])]
            raw = "".join(
                f"{name}: {item[key]}\r\n"
                for key, name in (("id", "Message-ID"), ("date", "Date"), ("subject", "Subject"),
                                  ("in_reply_to", "In-Reply-To"), ("references", "References"))
                if item.get(key)
            ).encode()
            return "OK", [(b"1 (BODY[HEADER.FIELDS ...]", raw), b")"]
        raise AssertionError(command)


def sent(to, date, subject, *, id="<staff@example.test>", in_reply_to=None, references=None):
    return {"to": to, "date": date, "subject": subject, "id": id,
            "in_reply_to": in_reply_to, "references": references}


def add_reply(db, *, created=BASE, recipient="charles@example.test", mode="sent"):
    sequence = db.query(Conversation).count() + 1
    conv = Conversation(
        channel="email", external_key=f"<t{sequence}@x>", customer_address=recipient
    )
    db.add(conv)
    db.flush()
    provider_id = CUSTOMER_ID if sequence == 1 else f"<c{sequence}@x>"
    message = Message(conversation_id=conv.id, channel="email", provider_id=provider_id,
                      direction="inbound", body="Hola", sender=recipient)
    db.add(message)
    db.flush()
    reply = AutoReply(message_id=message.id, conversation_id=conv.id, recipient=recipient,
                      mode=mode,
                      language="en", subject="Re: Stay", text="Hello", created_at=created,
                      provider_id=AUTO_ID if mode == "sent" else None)
    db.add(reply)
    db.commit()
    return reply, message


def test_latest_sent_picks_the_newest_staff_email_and_skips_auto_replies():
    imap = FakeImap({
        3: sent("charles@example.test", "Wed, 24 Apr 2024 09:50:37 +0200", "RE: Booking details"),
        8: sent("charles@example.test", "Mon, 16 Nov 2026 19:51:31 +0000", "Re: Stay", id=AUTO_ID),
        9: sent("charles@example.test", "Tue, 17 Nov 2026 14:15:10 +0100",
                "RE: Booking confirmation 130676", in_reply_to="<other@x>"),
        10: sent("someone@example.test", "Wed, 18 Nov 2026 10:00:00 +0100", "Other"),
    })
    found = latest_sent(imap, "charles@example.test", skip_ids={AUTO_ID})
    assert found["subject"] == "RE: Booking confirmation 130676"
    assert found["sent_at"] == datetime(2026, 11, 17, 13, 15, 10, tzinfo=UTC)
    # Read-only: searches and header fetches with PEEK, nothing else.
    assert {command[0] for command in imap.commands} == {"SEARCH", "FETCH"}
    assert all("BODY.PEEK" in command[2] for command in imap.commands if command[0] == "FETCH")


def test_unsafe_addresses_are_not_searched():
    imap = FakeImap({})
    assert latest_sent(imap, 'x" OR ALL "@example.test', skip_ids=set()) is None
    assert imap.commands == []


def test_view_tells_whether_the_team_wrote_after_and_in_which_thread(db):
    reply, message = add_reply(db)
    assert staff_reply_view(reply) is None  # not checked yet

    found = {"sent_at": BASE + timedelta(hours=17.4), "subject": "RE: Booking confirmation",
             "references": "<other@x>"}
    apply(reply, message.provider_id, found, BASE + timedelta(days=1))
    view = staff_reply_view(reply)
    assert view["after_auto_reply"] is True
    assert view["hours_after"] == 17.4
    assert view["in_thread"] is False

    found["references"] = f"<x> {CUSTOMER_ID}"
    apply(reply, message.provider_id, found, BASE + timedelta(days=1))
    assert staff_reply_view(reply)["in_thread"] is True

    apply(reply, message.provider_id, {"sent_at": BASE - timedelta(days=200), "subject": "Old",
                                       "references": ""}, BASE + timedelta(days=1))
    view = staff_reply_view(reply)
    assert view["after_auto_reply"] is False and view["hours_after"] is None

    apply(reply, message.provider_id, None, BASE + timedelta(days=1))
    assert staff_reply_view(reply)["last_sent_at"] is None


def test_filter_by_staff_reply(db):
    answered, message = add_reply(db)
    apply(answered, message.provider_id,
          {"sent_at": BASE + timedelta(hours=3), "subject": "RE", "references": ""}, BASE)
    waiting, message = add_reply(db, recipient="ana@example.test")
    apply(waiting, message.provider_id, None, BASE)
    add_reply(db, recipient="luis@example.test")  # not checked yet: in neither filter
    db.commit()

    after = list_automatic_messages(db, staff_reply="after")["items"]
    none = list_automatic_messages(db, staff_reply="none")["items"]
    assert [item["recipient"] for item in after] == ["charles@example.test"]
    assert [item["recipient"] for item in none] == ["ana@example.test"]
    assert after[0]["staff_reply"]["subject"] == "RE"


def test_due_rechecks_recent_and_checks_old_ones_once(db):
    current = BASE + timedelta(days=60)
    recent, _ = add_reply(db, created=current - timedelta(days=2))
    old, _ = add_reply(db, created=current - timedelta(days=50), recipient="old@example.test")
    assert {item.id for item in due(db, current)} == {recent.id, old.id}
    old.staff_checked_at = current
    recent.staff_checked_at = current
    db.commit()
    assert [item.id for item in due(db, current)] == [recent.id]


def test_sent_folder_uses_the_special_use_flag(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "auto_reply_sent_folder", "")
    imap = FakeImap({}, boxes=[b'(\\HasNoChildren) "/" "INBOX"',
                               b'(\\HasNoChildren \\Sent) "/" "Elementos enviados"'])
    assert sent_folder(imap) == "Elementos enviados"
