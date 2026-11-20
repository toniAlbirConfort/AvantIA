from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.auth import hasher
from app.automatic_messages import list_automatic_messages
from app.config import get_settings
from app.db import get_db
from app.main import app
from app.models import AuditEvent, AutoReply, Conversation, Message, User

BASE = datetime(2026, 11, 1, 22, 0, tzinfo=UTC)


def add_reply(db, *, mode="sent", recipient=None, name=None, hours=0, error=None):
    sequence = db.query(Conversation).count() + 1
    recipient = recipient or f"client{sequence}@example.test"
    conv = Conversation(
        channel="email",
        external_key=f"<thread-{sequence}@example.test>",
        customer_address=recipient,
        customer_name=name,
    )
    db.add(conv)
    db.flush()
    message = Message(
        conversation_id=conv.id,
        channel="email",
        provider_id=f"<msg-{sequence}@example.test>",
        direction="inbound",
        body="Hola",
        sender=recipient,
    )
    db.add(message)
    db.flush()
    reply = AutoReply(
        message_id=message.id,
        conversation_id=conv.id,
        recipient=recipient,
        mode=mode,
        language="es",
        subject=f"Re: Consulta {sequence}",
        text="Gracias por escribirnos.",
        error=error,
        created_at=BASE + timedelta(hours=hours),
    )
    db.add(reply)
    db.commit()
    return reply


def test_lists_auto_replies_newest_first_with_status_counts(db):
    add_reply(db, mode="sent", hours=0)
    add_reply(db, mode="shadow", hours=1)
    add_reply(db, mode="failed", hours=2, error="SMTPException: boom")
    add_reply(db, mode="sending", hours=3)

    result = list_automatic_messages(db)

    assert result["total"] == 4
    assert [item["status"] for item in result["items"]] == [
        "delivery_unknown",
        "failed",
        "shadow",
        "sent",
    ]
    assert result["counts"] == {"sent": 1, "shadow": 1, "failed": 1, "delivery_unknown": 1}
    first = result["items"][0]
    assert first["channel"] == "email"
    assert first["kind"] == "auto_reply"
    assert first["id"].startswith("auto_reply:")
    assert result["items"][1]["error"] == "SMTPException: boom"


def test_filters_keep_counts_for_every_status(db):
    add_reply(db, mode="sent", recipient="ana@example.test", name="Ana", hours=0)
    add_reply(db, mode="failed", recipient="ana.b@example.test", hours=1)
    add_reply(db, mode="sent", recipient="luis@example.test", hours=30)

    result = list_automatic_messages(db, status="sent", q="ana")
    assert [item["recipient"] for item in result["items"]] == ["ana@example.test"]
    assert result["total"] == 1
    assert result["counts"]["failed"] == 1

    window = list_automatic_messages(db, start=BASE + timedelta(hours=24))
    assert [item["recipient"] for item in window["items"]] == ["luis@example.test"]

    assert list_automatic_messages(db, channel="whatsapp")["items"] == []
    assert list_automatic_messages(db, channel="whatsapp")["counts"]["sent"] == 0


def test_flags_a_failed_copy_to_the_sent_folder(db):
    reply = add_reply(db, mode="sent")
    db.add(
        AuditEvent(
            conversation_id=reply.conversation_id,
            action="auto_reply.copy_failed",
            detail={"message_id": reply.message_id, "error_type": "IMAP4.error"},
        )
    )
    db.commit()
    assert list_automatic_messages(db)["items"][0]["copy_failed"] is True


def test_paginates(db):
    for hour in range(5):
        add_reply(db, hours=hour)
    page = list_automatic_messages(db, limit=2, offset=2)
    assert page["total"] == 5
    assert len(page["items"]) == 2


@pytest.fixture
def client_headers(db, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_secret", "unit-test-secret-with-more-than-32-characters")
    db.add(
        User(
            username="operator",
            password_hash=hasher.hash("very-long-test-password"),
            role="operator",
        )
    )
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            login = client.post(
                "/api/login",
                json={"username": "operator", "password": "very-long-test-password"},
            )
            yield client, {"Authorization": f"Bearer {login.json()['token']}"}
    finally:
        app.dependency_overrides.clear()


def test_endpoint_requires_login_and_validates_filters(db, client_headers):
    client, headers = client_headers
    add_reply(db, mode="shadow")

    assert client.get("/api/automatic-messages").status_code == 401
    response = client.get("/api/automatic-messages?status=shadow", headers=headers)
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["sources"][0]["kind"] == "auto_reply"
    assert client.get("/api/automatic-messages?status=x", headers=headers).status_code == 422
    assert client.get("/api/automatic-messages?channel=sms", headers=headers).status_code == 422
