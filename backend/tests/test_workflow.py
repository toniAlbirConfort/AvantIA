from __future__ import annotations

import asyncio
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import hasher
from app.config import get_settings
from app.db import get_db
from app.delivery import send_approved
from app.main import app
from app.models import Conversation, Draft, Job, Message, User
from app.processing import CLASSIFY_SYSTEM, EXTRACT_SYSTEM, analyze_message


def test_gowa_duplicate_is_classified_without_any_real_send(db, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_secret", "unit-test-secret-with-more-than-32-characters")
    monkeypatch.setattr(settings, "gowa_webhook_secret", "test-webhook-secret")
    monkeypatch.setattr(settings, "gowa_device_id", "test-device")
    db.add(
        User(
            username="operator",
            password_hash=hasher.hash("very-long-test-password"),
            role="operator",
        )
    )
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    sent = []

    async def fake_ollama(system, user, **kwargs):
        if system == CLASSIFY_SYSTEM:
            return {
                "classification": "demand",
                "confidence": "high",
                "reason": "Consulta disponibilidad.",
            }
        if system == EXTRACT_SYSTEM:
            return {
                "property_query": "A",
                "arrival": "2026-11-10",
                "departure": "2026-11-13",
                "guests": 2,
                "intent": "availability",
                "customer_language": "es",
            }
        return {"text": "Gracias por su consulta."}

    async def fake_send(chat, text):
        sent.append((chat, text))
        raise AssertionError("El modo de solo lectura debe bloquear el envío")

    monkeypatch.setattr("app.processing.ollama_json", fake_ollama)
    monkeypatch.setattr("app.delivery.gowa_send", fake_send)
    try:
        with TestClient(app) as client:
            event = {
                "event": "message",
                "device_id": "test-device",
                "payload": {
                    "id": "in-1",
                    "chat_id": "34600111222@s.whatsapp.net",
                    "from": "34600111222@s.whatsapp.net",
                    "body": "¿Está libre A?",
                },
            }
            raw = json.dumps(event).encode()
            sig = hmac.new(b"test-webhook-secret", raw, hashlib.sha256).hexdigest()
            headers = {"X-Hub-Signature-256": f"sha256={sig}"}
            first = client.post("/webhooks/gowa", content=raw, headers=headers)
            again = client.post("/webhooks/gowa", content=raw, headers=headers)
            assert first.status_code == 200 and not first.json()["duplicate"]
            assert again.status_code == 200 and again.json()["duplicate"]
            assert db.query(Message).count() == 1
            assert (
                client.post(
                    "/webhooks/gowa", content=raw, headers={"X-Hub-Signature-256": "sha256=bad"}
                ).status_code
                == 401
            )

            asyncio.run(analyze_message(db, first.json()["message_id"]))
            assert sent == []
            login = client.post(
                "/api/login", json={"username": "operator", "password": "very-long-test-password"}
            )
            auth = {"Authorization": f"Bearer {login.json()['token']}"}
            conv = db.scalar(select(Conversation))
            assert conv.status == "pending"
            approved = client.post(f"/api/conversations/{conv.id}/approve", headers=auth)
            assert approved.status_code == 423
            assert sent == []
            assert (
                client.post(f"/api/conversations/{conv.id}/approve", headers=auth).status_code
                == 423
            )
            draft = db.scalar(select(Draft))
            assert db.scalar(select(Job).where(Job.key == f"send:{draft.id}")) is None
            with pytest.raises(RuntimeError, match="solo lectura"):
                asyncio.run(send_approved(db, draft.id))
            assert sent == []
    finally:
        app.dependency_overrides.clear()


def test_read_only_mode_blocks_delivery_before_provider_call(db, monkeypatch):
    from app.models import now

    conv = Conversation(
        channel="whatsapp", external_key="34600111222@s.whatsapp.net", status="queued"
    )
    db.add(conv)
    db.flush()
    draft = Draft(conversation_id=conv.id, text="Respuesta", status="queued", approved_at=now())
    db.add(draft)
    db.commit()

    sent = []

    async def broken_send(chat, text):
        sent.append((chat, text))
        raise TimeoutError("connection lost")

    monkeypatch.setattr("app.delivery.gowa_send", broken_send)
    monkeypatch.setattr("app.delivery.smtp_send", lambda **kwargs: sent.append(kwargs))
    with pytest.raises(RuntimeError, match="solo lectura"):
        asyncio.run(send_approved(db, draft.id))
    assert draft.status == "queued"
    assert sent == []
    assert db.query(Message).count() == 0


def test_read_only_mode_blocks_approved_draft_even_after_new_inbound(db, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from app.models import now

    conv = Conversation(
        channel="whatsapp", external_key="34600111222@s.whatsapp.net", status="queued"
    )
    db.add(conv)
    db.flush()
    draft = Draft(
        conversation_id=conv.id,
        text="Respuesta antigua",
        status="queued",
        approved_at=datetime.now(UTC) - timedelta(minutes=2),
    )
    db.add(draft)
    db.commit()
    conv.last_inbound_at = now()
    db.commit()
    sent = []

    async def fake_send(chat, text):
        sent.append(text)

    monkeypatch.setattr("app.delivery.gowa_send", fake_send)
    monkeypatch.setattr("app.delivery.smtp_send", lambda **kwargs: sent.append(kwargs))
    with pytest.raises(RuntimeError, match="solo lectura"):
        asyncio.run(send_approved(db, draft.id))
    assert draft.status == "queued"
    assert sent == []


def test_availability_change_requires_draft_review(db, monkeypatch):
    from datetime import date, timedelta

    from app.availability import import_csv

    settings = get_settings()
    monkeypatch.setattr(settings, "app_secret", "unit-test-secret-with-more-than-32-characters")
    user = User(
        username="reviewer", password_hash=hasher.hash("very-long-test-password"), role="operator"
    )
    db.add(user)
    start = date.today() + timedelta(days=10)
    end = start + timedelta(days=3)
    conv = Conversation(
        channel="email",
        external_key="<request@example.test>",
        customer_address="client@example.test",
        status="pending",
        analysis={
            "property_query": "A",
            "arrival": str(start),
            "departure": str(end),
            "guests": 2,
            "availability": {
                "batch_id": None,
                "stale": True,
                "requested": "A",
                "status": "manual_check",
                "alternatives": [],
                "imported_at": None,
            },
        },
    )
    db.add(conv)
    db.flush()
    db.add(Draft(conversation_id=conv.id, text="Estamos comprobando fechas.", status="pending"))
    db.commit()
    raw = (
        "property_id,property_name,capacity,start_date,end_date,status\n"
        f"A,Apartamento A,4,{start},{end},available"
    ).encode()
    import_csv(db, raw=raw, filename="fresh.csv", source="upload")
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            login = client.post(
                "/api/login", json={"username": "reviewer", "password": "very-long-test-password"}
            )
            auth = {"Authorization": f"Bearer {login.json()['token']}"}
            assert (
                client.post(f"/api/conversations/{conv.id}/approve", headers=auth).status_code
                == 423
            )
            detail = client.get(f"/api/conversations/{conv.id}", headers=auth).json()
            assert detail["analysis"]["availability_changed"]
            # This test imports only the calendar; without fresh property and reservation
            # snapshots the current availability service correctly requires a manual check.
            assert detail["analysis"]["availability"]["status"] == "manual_check"
            edited = client.put(
                f"/api/conversations/{conv.id}/draft",
                headers=auth,
                json={"text": "Hemos revisado la disponibilidad actual."},
            )
            assert edited.status_code == 200
            assert (
                client.post(f"/api/conversations/{conv.id}/approve", headers=auth).status_code
                == 423
            )
    finally:
        app.dependency_overrides.clear()


def test_stale_draft_edit_is_rejected(db, monkeypatch):
    from datetime import timedelta

    from app.models import now

    settings = get_settings()
    monkeypatch.setattr(settings, "app_secret", "unit-test-secret-with-more-than-32-characters")
    db.add(
        User(
            username="reviewer",
            password_hash=hasher.hash("very-long-test-password"),
            role="operator",
        )
    )
    conv = Conversation(channel="email", external_key="<stale@example.test>", status="pending")
    db.add(conv)
    db.flush()
    old = Draft(conversation_id=conv.id, text="Texto anterior", status="pending")
    db.add(old)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            login = client.post(
                "/api/login", json={"username": "reviewer", "password": "very-long-test-password"}
            )
            auth = {"Authorization": f"Bearer {login.json()['token']}"}
            newer = Draft(conversation_id=conv.id, text="Nuevo borrador", status="pending")
            db.add(newer)
            db.commit()
            stale = client.put(
                f"/api/conversations/{conv.id}/draft",
                headers=auth,
                json={"text": "Sobrescritura", "expected_draft_id": old.id},
            )
            assert stale.status_code == 409
            assert newer.text == "Nuevo borrador"
            conv.last_inbound_at = now() + timedelta(seconds=1)
            db.commit()
            inbound = client.put(
                f"/api/conversations/{conv.id}/draft",
                headers=auth,
                json={"text": "Sobrescritura", "expected_draft_id": newer.id},
            )
            assert inbound.status_code == 409
            assert newer.text == "Nuevo borrador"
    finally:
        app.dependency_overrides.clear()
