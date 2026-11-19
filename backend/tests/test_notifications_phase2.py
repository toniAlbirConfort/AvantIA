from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import gowa, notifications
from app.auth import hasher
from app.config import get_settings
from app.db import get_db
from app.main import app
from app.models import (
    AuditEvent,
    Conversation,
    ImportBatch,
    Message,
    Notification,
    TariffStudy,
    User,
)

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


def active(db, key):
    return db.scalar(
        select(Notification).where(
            Notification.dedupe_key == key, Notification.resolved_at.is_(None)
        )
    )


def active_of(db, kind):
    return db.scalars(
        select(Notification).where(Notification.kind == kind, Notification.resolved_at.is_(None))
    ).all()


def add_user(db, username, role="operator"):
    user = User(username=username, password_hash="x", role=role)
    db.add(user)
    db.commit()
    return user


def add_conversation(db, status="pending", assigned=None):
    sequence = db.query(Conversation).count() + 1
    conv = Conversation(
        channel="email",
        external_key=f"<t-{sequence}@example.test>",
        status=status,
        assigned_user_id=assigned,
        last_inbound_at=NOW - timedelta(hours=1),
        created_at=NOW - timedelta(hours=1),
    )
    db.add(conv)
    db.commit()
    return conv


def assign(db, conv, user, actor):
    conv.assigned_user_id = user.id
    db.add(
        AuditEvent(
            actor_user_id=actor.id,
            conversation_id=conv.id,
            action="conversation.assigned",
            detail={"user_id": user.id},
            created_at=NOW,
        )
    )
    db.commit()


def test_personal_kinds_need_a_recipient_and_shared_ones_refuse_it(db):
    with pytest.raises(ValueError):
        notifications.raise_condition(db, "conversation.assigned", "k", "x")
    with pytest.raises(ValueError):
        notifications.raise_condition(db, "inbox.overdue", "k", "x", user_id=1)


def test_assignment_notifies_only_the_assignee(db):
    ana = add_user(db, "ana", "admin")
    bea = add_user(db, "bea")
    carla = add_user(db, "carla")
    conv = add_conversation(db)
    assign(db, conv, bea, ana)
    notifications.scan(db, NOW)

    [item] = active_of(db, "conversation.assigned")
    assert item.user_id == bea.id and item.body == "Asignada por ana."

    def personal(user):
        return [
            i["id"]
            for i in notifications.list_for_user(db, user)
            if i["kind"] == "conversation.assigned"
        ]

    assert personal(bea) == [item.id]
    assert personal(ana) == [] and personal(carla) == []

    assign(db, conv, carla, ana)
    notifications.scan(db, NOW)
    [moved] = active_of(db, "conversation.assigned")
    assert moved.user_id == carla.id and item.status == "resolved"

    conv.status = "sent"
    db.commit()
    notifications.scan(db, NOW)
    assert active_of(db, "conversation.assigned") == []


def test_self_assignment_is_not_notified(db):
    bea = add_user(db, "bea")
    conv = add_conversation(db)
    assign(db, conv, bea, bea)
    notifications.scan(db, NOW)
    assert active_of(db, "conversation.assigned") == []


def add_audit(db, action, minutes_ago, **detail):
    event = AuditEvent(
        action=action, detail=detail, created_at=NOW - timedelta(minutes=minutes_ago)
    )
    db.add(event)
    db.commit()
    return event


def test_llm_failing_needs_several_failures_without_success(db):
    for minutes in (20, 10):
        add_audit(db, "message.classification_failed", minutes)
    notifications.scan(db, NOW)
    assert active(db, "llm.failing") is None

    add_audit(db, "message.classification_failed", 5)
    notifications.scan(db, NOW)
    assert active(db, "llm.failing").title == "El análisis con IA está fallando"

    add_audit(db, "message.classified", 1)
    notifications.scan(db, NOW)
    assert active(db, "llm.failing") is None


def test_classification_review_is_one_counted_notice(db):
    conv = add_conversation(db)
    conv.classification_open = True
    for index, confidence in enumerate(("low", "medium", "high")):
        db.add(
            Message(
                conversation_id=conv.id,
                channel="email",
                provider_id=f"<m-{index}@example.test>",
                direction="inbound",
                body="Hola",
                classification_label="demand",
                classification_confidence=confidence,
                created_at=NOW - timedelta(hours=1),
            )
        )
    db.commit()
    notifications.scan(db, NOW)
    item = active(db, "classification.review")
    assert item.title == "1 conversación con la clasificación por revisar"
    assert item.body.endswith("Recibidas desde el 12/11: #1.")
    assert item.link == "/inbox?classification=review&from=2026-11-12"

    conv.classification_open = False
    db.commit()
    notifications.scan(db, NOW)
    assert active(db, "classification.review") is None


def add_study(db, status, error=None):
    study = TariffStudy(
        name="Verano 2027",
        period_start=date(2027, 6, 1),
        period_end=date(2027, 9, 30),
        guests=2,
        nights=7,
        status=status,
        error=error,
        created_at=NOW - timedelta(hours=1),
        completed_at=NOW - timedelta(minutes=5) if status != "failed" else None,
    )
    db.add(study)
    db.commit()
    return study


def test_tariff_studies_notify_once_with_their_severity(db):
    done = add_study(db, "completed")
    failed = add_study(db, "failed", "ValueError: sin datos de mercado")
    notifications.scan(db, NOW)
    finished = active(db, f"tariff.study:{done.id}")
    broken = active(db, f"tariff.study:{failed.id}")
    assert finished.severity == "info" and "completado" in finished.title
    assert broken.severity == "critical" and "sin datos de mercado" in broken.body

    notifications.clear_condition(db, f"tariff.study:{done.id}", NOW)
    notifications.scan(db, NOW)
    assert active(db, f"tariff.study:{done.id}") is None


def add_batch(db, dataset, warnings, minutes_ago):
    batch = ImportBatch(
        dataset=dataset,
        source="upload",
        filename="f.csv",
        checksum=f"{dataset}-{minutes_ago}",
        status="valid",
        warnings=warnings,
        created_at=NOW - timedelta(minutes=minutes_ago),
    )
    db.add(batch)
    db.commit()
    return batch


def test_import_warnings_follow_the_latest_valid_batch(db):
    add_batch(db, "customers", [{"code": "invalid_date"}, {"code": "short_row"}], 30)
    notifications.scan(db, NOW)
    item = active(db, "import.warnings:customers")
    assert item.title == "La última importación de clientes trae 2 advertencias"

    add_batch(db, "customers", [], 1)
    notifications.scan(db, NOW)
    assert active(db, "import.warnings:customers") is None


def test_automatic_repairs_and_reservation_warnings_do_not_notify(db):
    add_batch(db, "customers", [{"code": "extra_separators"}] * 37, 30)
    add_batch(db, "reservations", [{"code": "balance_mismatch", "locator": "X"}], 30)
    notifications.scan(db, NOW)
    assert active_of(db, "import.warnings") == []


def test_maintenance_actions_are_reported_once(db):
    admin = add_user(db, "ana", "admin")
    event = add_audit(db, "maintenance.payment_data_masked", 10, messages=3, conversations=2)
    purge = add_audit(db, "whatsapp.session.purged", 5)
    purge.actor_user_id = admin.id
    db.commit()
    notifications.scan(db, NOW)
    masked = active(db, f"maintenance:{event.id}")
    assert masked.body == "3 mensajes y 2 conversaciones."
    assert active(db, f"maintenance:{purge.id}").body.endswith("Lo hizo ana.")
    notifications.scan(db, NOW)
    assert len(active_of(db, "maintenance.done")) == 2


def test_auto_reply_and_recovered_jobs_are_events(db):
    notifications.notify_auto_reply_failed(db, 7, "SMTPException")
    notifications.notify_auto_reply_failed(db, 8, "SMTPException")
    notifications.notify_auto_reply_failed(db, 8, "IMAP4.error", copy=True)
    notifications.notify_job_recovered(db, "send", 3)
    db.commit()
    failed = active(db, "auto_reply.failed")
    assert failed.occurrences == 2 and failed.link == "/inbox/8"
    assert active(db, "auto_reply.copy_failed").occurrences == 1
    assert "puede haber salido" in active(db, "job.recovered:send").body


def run_gowa(db, monkeypatch, *, configured=True, status=None, error=None):
    async def fake_status(settings=None):
        if error:
            raise error
        return status

    monkeypatch.setattr(gowa, "gowa_is_configured", lambda settings=None: configured)
    monkeypatch.setattr(gowa, "get_gowa_status", fake_status)
    asyncio.run(notifications.watch_gowa(db))
    return active(db, "whatsapp")


def test_watch_gowa_tracks_the_whatsapp_device(db, monkeypatch):
    down = run_gowa(db, monkeypatch, error=gowa.GowaServiceUnavailable())
    assert down.severity == "critical" and "GowaServiceUnavailable" in down.body

    logged_out = {"registered": True, "is_logged_in": False, "is_connected": False}
    assert run_gowa(db, monkeypatch, status=logged_out).title.endswith("sesión iniciada")

    connected = {"registered": True, "is_logged_in": True, "is_connected": True}
    assert run_gowa(db, monkeypatch, status=connected) is None

    run_gowa(db, monkeypatch, error=gowa.GowaServiceUnavailable())
    assert run_gowa(db, monkeypatch, configured=False) is None


def test_muted_kinds_stay_listed_but_do_not_count(db):
    bea = add_user(db, "bea")
    notifications.raise_condition(db, "inbox.overdue", "inbox.overdue", "Pendientes", at=NOW)
    notifications.raise_condition(db, "classification.review", "classification.review", "x")
    db.commit()
    assert notifications.counts(db, bea)["unread"] == 2

    notifications.set_muted(db, bea, ["classification.review"])
    assert notifications.counts(db, bea)["unread"] == 1
    listed = {item["kind"]: item["muted"] for item in notifications.list_for_user(db, bea)}
    assert listed == {"inbox.overdue": False, "classification.review": True}

    with pytest.raises(ValueError):
        notifications.set_muted(db, bea, ["delivery.unknown"])  # critical
    with pytest.raises(ValueError):
        notifications.set_muted(db, bea, ["import.warnings"])  # admin only
    notifications.set_muted(db, bea, [])
    assert notifications.counts(db, bea)["unread"] == 2


@pytest.fixture
def client(db, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_secret", "unit-test-secret-with-more-than-32-characters")
    db.add(User(username="bea", password_hash=hasher.hash("very-long-test-password")))
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as test_client:
            login = test_client.post(
                "/api/login", json={"username": "bea", "password": "very-long-test-password"}
            )
            yield test_client, {"Authorization": f"Bearer {login.json()['token']}"}
    finally:
        app.dependency_overrides.clear()


def test_api_preferences(client):
    test_client, headers = client
    kinds = {
        item["kind"]: item
        for item in test_client.get("/api/notifications/preferences", headers=headers).json()
    }
    assert "conversation.assigned" in kinds and "mail.imap_failed" not in kinds
    assert kinds["delivery.unknown"]["can_mute"] is False if "delivery.unknown" in kinds else True

    saved = test_client.put(
        "/api/notifications/preferences", json={"muted": ["inbox.overdue"]}, headers=headers
    ).json()
    assert {item["kind"] for item in saved if item["muted"]} == {"inbox.overdue"}
    refused = test_client.put(
        "/api/notifications/preferences", json={"muted": ["mail.imap_failed"]}, headers=headers
    )
    assert refused.status_code == 422
