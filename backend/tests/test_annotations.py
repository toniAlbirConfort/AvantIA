from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import annotation_eval
from app.annotations import INTENTS, clean_expected, save_annotation, split_for, summary
from app.auth import hasher
from app.config import get_settings
from app.db import get_db
from app.main import app
from app.models import AuditEvent, Conversation, Job, Message, MessageAnnotation, User


def add_email(db, *, body="Hola", kind="inquiry", label=None, data=None):
    sequence = db.query(Conversation).count() + 1
    conversation = Conversation(
        channel="email",
        external_key=f"<thread-{sequence}@example.test>",
        kind=kind,
        customer_address=f"client{sequence}@example.test",
    )
    db.add(conversation)
    db.flush()
    message = Message(
        conversation_id=conversation.id,
        channel="email",
        provider_id=f"<msg-{sequence}@example.test>",
        direction="inbound",
        body=body,
        sender=conversation.customer_address,
        classification_label=label,
        classification_confidence="high" if label else None,
        classification_data=data,
        classification_model="test-model" if label else None,
    )
    db.add(message)
    db.commit()
    return conversation, message


@pytest.fixture
def client_headers(db, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_secret", "unit-test-secret-with-more-than-32-characters")
    db.add(User(username="operator", password_hash=hasher.hash("very-long-test-password"),
                role="operator"))
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


def test_every_intent_maps_to_a_queue_label():
    assert {intent.label for intent in INTENTS.values()} == {
        "demand",
        "guest_service",
        "non_demand",
    }
    assert {key for key, intent in INTENTS.items() if intent.needs_availability} == {
        "availability_request",
        "stay_change",
    }


def test_expected_data_is_validated_and_ignored_when_not_needed():
    assert clean_expected("marketing", {"arrival": "2027-01-01"}) is None
    assert clean_expected(
        "availability_request",
        {"arrival": "2027-02-20", "departure": "2027-03-12", "guests": 2, "property_query": " "},
    ) == {"arrival": "2027-02-20", "departure": "2027-03-12", "guests": 2,
          "property_query": None}
    assert clean_expected("availability_request", {}) == {
        "arrival": None, "departure": None, "guests": None, "property_query": None
    }
    with pytest.raises(ValueError):
        clean_expected("availability_request", {"arrival": "2027-03-12",
                                                 "departure": "2027-02-20"})
    with pytest.raises(ValueError):
        clean_expected("availability_request", {"arrival": "20/02/2027"})
    with pytest.raises(ValueError):
        clean_expected("stay_change", {"guests": 0})


def test_split_is_stable_and_keeps_some_messages_for_evaluation():
    splits = [split_for(item) for item in range(1, 201)]
    assert splits == [split_for(item) for item in range(1, 201)]
    assert 20 <= splits.count("eval") <= 60


def test_annotation_snapshots_the_model_and_updates_the_queue(db, client_headers):
    client, headers = client_headers
    conversation, message = add_email(db, body="Spam", label="non_demand")

    response = client.put(
        f"/api/messages/{message.id}/annotation",
        headers=headers,
        json={
            "intent": "availability_request",
            "expected": {"arrival": "2027-02-20", "departure": "2027-03-12", "guests": 2},
            "note": "Pide terraza soleada",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["active_classification"] == "demand"
    annotation = payload["messages"][0]["annotation"]
    assert annotation["intent"] == "availability_request"
    assert annotation["complete"] is True
    stored = db.scalar(select(MessageAnnotation))
    assert stored.predicted_label == "non_demand"
    assert stored.predicted_model == "test-model"
    assert message.classification_override == "demand"
    assert db.scalar(select(Job).where(Job.kind == "analyze")) is not None
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "message.annotated"))

    # A second edit keeps the original model snapshot.
    message.classification_label = "demand"
    db.commit()
    again = client.put(
        f"/api/messages/{message.id}/annotation",
        headers=headers,
        json={"intent": "lost_interest"},
    )
    assert again.status_code == 200
    assert again.json()["active_classification"] == "non_demand"
    assert db.scalar(select(MessageAnnotation)).predicted_label == "non_demand"
    assert db.scalar(select(MessageAnnotation)).expected is None
    assert db.query(MessageAnnotation).count() == 1


def test_booking_notifications_can_be_annotated_without_changing_their_flow(db, client_headers):
    client, headers = client_headers
    conversation, message = add_email(db, kind="booking")

    response = client.put(
        f"/api/messages/{message.id}/annotation",
        headers=headers,
        json={"intent": "platform_notice"},
    )

    assert response.status_code == 200
    assert message.classification_override is None
    assert response.json()["messages"][0]["annotation"]["intent"] == "platform_notice"


def test_invalid_annotations_are_rejected(db, client_headers):
    client, headers = client_headers
    _, message = add_email(db)
    assert client.put(f"/api/messages/{message.id}/annotation", headers=headers,
                      json={"intent": "nope"}).status_code == 422
    assert client.put(
        f"/api/messages/{message.id}/annotation",
        headers=headers,
        json={"intent": "availability_request",
              "expected": {"arrival": "2027-03-12", "departure": "2027-02-20"}},
    ).status_code == 422
    assert db.query(MessageAnnotation).count() == 0
    assert client.put(f"/api/messages/{message.id}/annotation",
                      json={"intent": "marketing"}).status_code == 401


def test_summary_compares_stored_predictions_with_annotations(db, client_headers):
    client, headers = client_headers
    extracted = {"arrival": "2027-02-20", "departure": "2027-03-10", "guests": 2,
                 "property_query": None}
    _, good = add_email(db, label="demand", data=extracted)
    _, wrong = add_email(db, label="demand")
    _, missing = add_email(db)
    expected = {"arrival": "2027-02-20", "departure": "2027-03-12", "guests": 2}
    save_annotation(db, good, intent="availability_request", expected=expected, note=None,
                    user_id=None)
    save_annotation(db, wrong, intent="marketing", expected=None, note=None, user_id=None)
    save_annotation(db, missing, intent="courtesy", expected=None, note=None, user_id=None)
    db.commit()

    result = summary(db)

    assert result["total"] == 3
    assert result["complete_availability"] == 1
    model = result["stored_model"]
    assert model["with_prediction"] == 2
    assert model["label_accuracy"] == 0.5
    assert model["confusion"] == {"non_demand->demand": 1}
    assert model["extraction"]["arrival"] == {"total": 1, "correct": 1}
    assert model["extraction"]["departure"] == {"total": 1, "correct": 0}
    assert model["extraction"]["property_query"] == {"total": 0, "correct": 0}

    response = client.get("/api/annotations/summary", headers=headers)
    assert response.status_code == 200
    assert response.json()["total"] == 3
    assert len(client.get("/api/annotations/intents", headers=headers).json()) == len(INTENTS)


def test_live_evaluation_reruns_the_model_without_writing(db, monkeypatch):
    _, message = add_email(db, body="¿Disponible del 20 feb al 12 mar para 2?", label="non_demand")
    save_annotation(
        db, message, intent="availability_request",
        expected={"arrival": "2027-02-20", "departure": "2027-03-12", "guests": 2},
        note=None, user_id=None,
    )
    db.commit()

    async def fake_ollama(system, user, *, model_name=None):
        if "Clasifica" in system:
            return {"classification": "demand", "confidence": "high", "reason": "Consulta"}
        return {"arrival": "2027-02-20", "departure": "2027-03-12", "guests": 2,
                "intent": "availability", "customer_language": "es"}

    monkeypatch.setattr(annotation_eval, "ollama_json", fake_ollama)

    report = asyncio.run(annotation_eval.live_report(db, "all", None))

    assert report["label_accuracy"] == 1.0
    assert report["extraction"]["departure"] == {"total": 1, "correct": 1}
    assert message.classification_label == "non_demand"
    stored = annotation_eval.stored_report(db, "all")
    assert stored["label_accuracy"] == 0.0
