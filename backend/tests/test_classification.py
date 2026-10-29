from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import hasher
from app.classification import resolve_conversation_classification
from app.config import READ_ONLY_MODE, get_settings
from app.db import get_db
from app.delivery import send_approved
from app.main import app
from app.models import AuditEvent, BookingTask, Conversation, Draft, Job, Message, User
from app.processing import CLASSIFY_SYSTEM, EXTRACT_SYSTEM, analyze_message


def add_inbound(
    db,
    *,
    body="¿Hay disponibilidad?",
    kind="inquiry",
    channel="whatsapp",
    prior_message_body=None,
):
    sequence = db.query(Conversation).count() + 1
    external_key = (
        f"client{sequence}@example.test"
        if channel == "email"
        else f"3460000000{sequence}@s.whatsapp.net"
    )
    conversation = Conversation(
        channel=channel,
        external_key=external_key,
        kind=kind,
        customer_address=external_key,
    )
    db.add(conversation)
    db.flush()
    if prior_message_body:
        db.add(
            Message(
                conversation_id=conversation.id,
                channel=channel,
                provider_id=f"context-{conversation.id}",
                direction="inbound",
                body=prior_message_body,
                sender=conversation.customer_address,
            )
        )
        db.flush()
    message = Message(
        conversation_id=conversation.id,
        channel=conversation.channel,
        provider_id=f"in-{conversation.id}-{db.query(Message).count() + 1}",
        direction="inbound",
        body=body,
        sender=conversation.customer_address,
    )
    db.add(message)
    db.commit()
    return conversation, message


def fake_extraction():
    return {
        "property_query": "Apartamento A",
        "arrival": "2026-12-10",
        "departure": "2026-12-13",
        "guests": 2,
        "intent": "availability",
        "customer_language": "es",
    }


@pytest.mark.parametrize("channel", ["whatsapp", "email"])
def test_high_confidence_demand_creates_draft_without_sending(db, monkeypatch, channel):
    conversation, message = add_inbound(
        db,
        channel=channel,
        prior_message_body="¿Podemos continuar con lo que hablamos ayer?",
    )
    previous_message = db.scalar(
        select(Message)
        .where(Message.conversation_id == conversation.id, Message.id < message.id)
        .order_by(Message.id.desc())
    )
    systems = []
    sent = []

    async def fake_ollama(system, user, **kwargs):
        systems.append(system)
        if system == CLASSIFY_SYSTEM:
            context = json.loads(user)["messages"]
            assert context[-2]["text"] == previous_message.body
            assert context[-1]["text"] == message.body
            return {
                "classification": "demand",
                "confidence": "high",
                "reason": "Consulta disponibilidad.",
            }
        if system == EXTRACT_SYSTEM:
            return fake_extraction()
        return {"text": "Respuesta de prueba sin datos de envío."}

    def unexpected_send(*args, **kwargs):
        sent.append((args, kwargs))
        raise AssertionError("La clasificación no puede invocar canales de salida")

    monkeypatch.setattr("app.processing.ollama_json", fake_ollama)
    monkeypatch.setattr("app.delivery.gowa_send", unexpected_send)
    monkeypatch.setattr("app.delivery.smtp_send", unexpected_send)

    asyncio.run(analyze_message(db, message.id))

    assert message.classification_label == "demand"
    assert previous_message.classification_label is None
    assert message.classification_confidence == "high"
    assert conversation.active_classification == "demand"
    assert conversation.classification_open is True
    assert db.scalar(select(Draft).where(Draft.source_message_id == message.id)) is not None
    assert db.scalar(select(Job).where(Job.kind == "send")) is None
    assert not sent
    assert READ_ONLY_MODE is True

    draft = db.scalar(select(Draft).where(Draft.source_message_id == message.id))
    with pytest.raises(RuntimeError, match="solo lectura"):
        asyncio.run(send_approved(db, draft.id))
    assert not sent


@pytest.mark.parametrize(
    ("category", "expected_open"),
    [("guest_service", True), ("non_demand", False)],
)
def test_non_demand_and_guest_service_do_not_create_drafts(
    db, monkeypatch, category, expected_open
):
    conversation, message = add_inbound(db, body="Mensaje de prueba")
    systems = []

    async def fake_ollama(system, user, **kwargs):
        systems.append(system)
        return {"classification": category, "confidence": "high", "reason": "Clase clara."}

    monkeypatch.setattr("app.processing.ollama_json", fake_ollama)
    asyncio.run(analyze_message(db, message.id))

    assert message.classification_label == category
    assert conversation.active_classification == category
    assert conversation.classification_open is expected_open
    assert db.scalar(select(Draft).where(Draft.source_message_id == message.id)) is None
    assert systems == [CLASSIFY_SYSTEM]


def test_low_confidence_and_model_failure_are_visible_for_review(db, monkeypatch):
    conversation, message = add_inbound(db, body="Quizá me interesa, no lo sé")

    async def low_confidence(system, user, **kwargs):
        return {"classification": "demand", "confidence": "medium", "reason": "Falta concretar."}

    monkeypatch.setattr("app.processing.ollama_json", low_confidence)
    asyncio.run(analyze_message(db, message.id))
    assert message.classification_label == "demand"
    assert conversation.active_classification == "review"
    assert conversation.classification_open is True
    assert db.scalar(select(Draft).where(Draft.source_message_id == message.id)) is None

    failed_conversation, failed_message = add_inbound(db, body="Consulta sin contexto")

    async def unavailable(system, user, **kwargs):
        raise RuntimeError("mensaje y datos privados no deben guardarse")

    monkeypatch.setattr("app.processing.ollama_json", unavailable)
    asyncio.run(analyze_message(db, failed_message.id))
    assert failed_message.classification_label == "uncertain"
    assert failed_message.classification_confidence == "low"
    assert (
        failed_message.classification_reason
        == "No se pudo completar la clasificación automática."
    )
    assert failed_conversation.active_classification == "review"
    assert db.scalar(select(Draft).where(Draft.source_message_id == failed_message.id)) is None


def test_open_demand_stays_active_after_thanks_and_new_demand_reopens(db, monkeypatch):
    conversation, demand_message = add_inbound(db, body="Busco alojamiento para diciembre")
    categories = iter(["demand", "non_demand", "demand"])

    async def fake_ollama(system, user, **kwargs):
        if system == CLASSIFY_SYSTEM:
            return {
                "classification": next(categories),
                "confidence": "high",
                "reason": "Contexto claro.",
            }
        return fake_extraction()

    monkeypatch.setattr("app.processing.ollama_json", fake_ollama)
    asyncio.run(analyze_message(db, demand_message.id))
    assert conversation.active_classification == "demand"
    assert conversation.classification_open is True

    thanks = Message(
        conversation_id=conversation.id,
        channel="whatsapp",
        provider_id="thanks-1",
        direction="inbound",
        body="Gracias",
        sender=conversation.customer_address,
    )
    db.add(thanks)
    db.commit()
    asyncio.run(analyze_message(db, thanks.id))
    assert thanks.classification_label == "non_demand"
    assert conversation.active_classification == "demand"
    assert conversation.classification_open is True

    resolve_conversation_classification(conversation, outcome="handled", actor_user_id=1)
    db.commit()
    assert conversation.classification_open is False

    follow_up = Message(
        conversation_id=conversation.id,
        channel="whatsapp",
        provider_id="demand-2",
        direction="inbound",
        body="¿Podemos reservar?",
        sender=conversation.customer_address,
    )
    db.add(follow_up)
    db.commit()
    asyncio.run(analyze_message(db, follow_up.id))
    assert conversation.active_classification == "demand"
    assert conversation.classification_open is True


def test_booking_notifications_keep_the_existing_workflow(db, monkeypatch):
    conversation, message = add_inbound(db, body="Nueva reserva", kind="booking")
    systems = []

    async def fake_ollama(system, user, **kwargs):
        systems.append(system)
        return fake_extraction()

    monkeypatch.setattr("app.processing.ollama_json", fake_ollama)
    asyncio.run(analyze_message(db, message.id))

    assert systems == [EXTRACT_SYSTEM]
    assert message.classification_label is None
    assert db.scalar(select(BookingTask).where(BookingTask.conversation_id == conversation.id))
    assert db.scalar(select(Draft).where(Draft.source_message_id == message.id)) is None


def test_operator_correction_and_resolution_are_audited_and_read_only(db, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_secret", "unit-test-secret-with-more-than-32-characters")
    operator = User(
        username="operator",
        password_hash=hasher.hash("very-long-test-password"),
        role="operator",
    )
    db.add(operator)
    conversation, message = add_inbound(db, body="Sin clasificar")
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            assert client.patch(
                f"/api/messages/{message.id}/classification",
                json={"label": "demand"},
            ).status_code == 401
            login = client.post(
                "/api/login",
                json={"username": "operator", "password": "very-long-test-password"},
            )
            headers = {"Authorization": f"Bearer {login.json()['token']}"}
            unclassified_filter = client.get(
                "/api/inbox?classification=unclassified", headers=headers
            )
            assert [item["id"] for item in unclassified_filter.json()["items"]] == [conversation.id]

            corrected = client.patch(
                f"/api/messages/{message.id}/classification",
                headers=headers,
                json={"label": "demand"},
            )
            assert corrected.status_code == 200
            assert corrected.json()["active_classification"] == "demand"
            assert corrected.json()["classification_open"] is True
            assert db.scalar(select(Job).where(Job.kind == "analyze")) is not None
            assert message.classification_label is None
            assert message.classification_override == "demand"
            demand_filter = client.get("/api/inbox?classification=demand", headers=headers)
            assert [item["id"] for item in demand_filter.json()["items"]] == [conversation.id]
            job_count = db.query(Job).filter_by(kind="analyze").count()
            assert client.patch(
                f"/api/messages/{message.id}/classification",
                headers=headers,
                json={"label": "demand"},
            ).status_code == 200
            assert db.query(Job).filter_by(kind="analyze").count() == job_count

            db.add(Draft(conversation_id=conversation.id, source_message_id=message.id,
                         text="Borrador", status="pending"))
            db.commit()
            changed = client.patch(
                f"/api/messages/{message.id}/classification",
                headers=headers,
                json={"label": "non_demand"},
            )
            assert changed.status_code == 200
            assert changed.json()["active_classification"] == "non_demand"
            assert changed.json()["classification_open"] is False
            assert message.classification_label is None
            assert message.classification_override == "non_demand"
            assert message.classification_reviewed_by_id == operator.id
            non_demand_filter = client.get(
                "/api/inbox?classification=non_demand", headers=headers
            )
            assert [item["id"] for item in non_demand_filter.json()["items"]] == [conversation.id]
            draft = db.scalar(select(Draft).where(Draft.source_message_id == message.id))
            assert draft.status == "superseded"
            assert db.scalar(
                select(AuditEvent).where(
                    AuditEvent.conversation_id == conversation.id,
                    AuditEvent.action == "message.classification_corrected",
                )
            ) is not None

            renewed = Message(
                conversation_id=conversation.id,
                channel="whatsapp",
                provider_id="new-demand-after-correction",
                direction="inbound",
                body="Busco otra estancia",
                sender=conversation.customer_address,
            )
            db.add(renewed)
            db.commit()
            reopened = client.patch(
                f"/api/messages/{renewed.id}/classification",
                headers=headers,
                json={"label": "demand"},
            )
            assert reopened.json()["classification_open"] is True
            handled = client.post(
                f"/api/conversations/{conversation.id}/resolve",
                headers=headers,
                json={"outcome": "handled"},
            )
            assert handled.status_code == 200
            assert handled.json()["classification_open"] is False
            assert handled.json()["classification_resolved_by_id"] == operator.id
            assert db.scalar(
                select(AuditEvent).where(
                    AuditEvent.conversation_id == conversation.id,
                    AuditEvent.action == "conversation.classification_resolved",
                )
            ) is not None
            not_demand_message = Message(
                conversation_id=conversation.id,
                channel="whatsapp",
                provider_id="second-demand-to-resolve",
                direction="inbound",
                body="Necesito otra estancia",
                sender=conversation.customer_address,
            )
            db.add(not_demand_message)
            db.commit()
            assert client.patch(
                f"/api/messages/{not_demand_message.id}/classification",
                headers=headers,
                json={"label": "demand"},
            ).status_code == 200
            db.add(
                Draft(
                    conversation_id=conversation.id,
                    source_message_id=not_demand_message.id,
                    text="Borrador pendiente",
                    status="pending",
                )
            )
            db.commit()
            not_demand = client.post(
                f"/api/conversations/{conversation.id}/resolve",
                headers=headers,
                json={"outcome": "not_demand"},
            )
            assert not_demand.status_code == 200
            assert not_demand.json()["active_classification"] == "non_demand"
            assert not_demand.json()["classification_open"] is False
            assert not_demand_message.classification_override == "non_demand"
            assert (
                db.scalar(
                    select(Draft).where(Draft.source_message_id == not_demand_message.id)
                ).status
                == "superseded"
            )
            assert client.post(
                f"/api/conversations/{conversation.id}/resolve",
                headers=headers,
                json={"outcome": "handled"},
            ).status_code == 409
            assert db.scalar(select(Job).where(Job.kind == "send")) is None
    finally:
        app.dependency_overrides.clear()
