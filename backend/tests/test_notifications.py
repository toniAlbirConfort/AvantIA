from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import notifications
from app.auth import hasher
from app.config import get_settings
from app.db import get_db
from app.main import app
from app.models import (
    BookingTask,
    Conversation,
    ImportBatch,
    MailCursor,
    Message,
    Notification,
    NotificationRead,
    RegistrationCheck,
    ReservationSyncState,
    User,
)

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


def active(db, key):
    return db.scalar(
        select(Notification).where(
            Notification.dedupe_key == key, Notification.resolved_at.is_(None)
        )
    )


def add_user(db, username="operator", role="operator"):
    user = User(username=username, password_hash="x", role=role)
    db.add(user)
    db.commit()
    return user


def add_conversation(db, status="pending", inbound=NOW, kind="inquiry"):
    sequence = db.query(Conversation).count() + 1
    conv = Conversation(
        channel="email",
        external_key=f"<t-{sequence}@example.test>",
        kind=kind,
        status=status,
        last_inbound_at=inbound,
        created_at=inbound,
    )
    db.add(conv)
    db.flush()
    message = Message(
        conversation_id=conv.id,
        channel="email",
        provider_id=f"<m-{sequence}@example.test>",
        direction="inbound",
        body="Hola",
        sender="client@example.test",
    )
    db.add(message)
    db.commit()
    return conv, message


def test_condition_lifecycle_and_dismiss_stays_quiet_until_cleared(db):
    item = notifications.raise_condition(db, "avantio.sync_failed", "k", "Falla", at=NOW)
    again = notifications.raise_condition(db, "avantio.sync_failed", "k", "Falla 2", at=NOW)
    assert again.id == item.id and again.title == "Falla 2" and again.occurrences == 1

    notifications.dismiss(db, add_user(db, role="admin"), item)
    quiet = notifications.raise_condition(db, "avantio.sync_failed", "k", "Falla", at=NOW)
    assert quiet.id == item.id and quiet.status == "dismissed"

    assert notifications.clear_condition(db, "k", NOW) == 1
    assert item.status == "dismissed" and item.resolved_at is not None
    fresh = notifications.raise_condition(db, "avantio.sync_failed", "k", "Falla", at=NOW)
    assert fresh.id != item.id and fresh.status == "open"


def test_clear_marks_open_condition_as_resolved(db):
    item = notifications.raise_condition(db, "mail.imap_failed", "mail.imap", "x", at=NOW)
    notifications.clear_condition(db, "mail.imap", NOW)
    assert item.status == "resolved" and item.resolved_at == NOW


def test_unknown_kind_is_rejected(db):
    with pytest.raises(ValueError):
        notifications.raise_condition(db, "nope", "k", "x")
    with pytest.raises(ValueError):
        notifications.record_event(db, "nope", "k", "x")


def test_event_repeats_add_occurrences_and_become_unread(db):
    admin = add_user(db, "admin", "admin")
    notifications.notify_job_failed(db, "analyze", 1, "TimeoutError: ollama")
    db.commit()
    item = db.scalar(select(Notification))
    notifications.mark_read(db, admin)
    assert notifications.counts(db, admin)["unread"] == 0

    notifications.notify_job_failed(db, "analyze", 2, "TimeoutError: ollama")
    db.commit()
    assert db.query(Notification).count() == 1
    assert item.occurrences == 2 and "#2" in item.body
    assert notifications.counts(db, admin)["unread"] == 1

    notifications.dismiss(db, admin, item)
    notifications.notify_job_failed(db, "analyze", 3, "boom")
    db.commit()
    assert db.query(Notification).count() == 2


def test_audience_limits_what_operators_see(db):
    operator = add_user(db)
    admin = add_user(db, "admin", "admin")
    notifications.raise_condition(db, "mail.imap_failed", "mail.imap", "Correo", at=NOW)
    notifications.raise_condition(db, "inbox.overdue", "inbox.overdue", "Pendientes", at=NOW)
    db.commit()
    assert [i["kind"] for i in notifications.list_for_user(db, operator)] == ["inbox.overdue"]
    assert notifications.counts(db, admin) == {
        "unread": 2,
        "by_severity": {"critical": 1, "action": 1, "info": 0},
    }
    hidden = active(db, "mail.imap")
    assert notifications.get_visible(db, operator, hidden.id) is None


def test_scan_tracks_uncertain_deliveries(db):
    conv, _ = add_conversation(db, status="delivery_unknown")
    notifications.scan(db, NOW)
    item = active(db, f"delivery.unknown:{conv.id}")
    assert item.severity == "critical" and item.link == f"/inbox/{conv.id}"

    conv.status = "sent"
    db.commit()
    notifications.scan(db, NOW)
    assert active(db, f"delivery.unknown:{conv.id}") is None
    assert item.status == "resolved"


def test_scan_aggregates_overdue_consultations(db):
    add_conversation(db, inbound=NOW - timedelta(minutes=40))
    add_conversation(db, inbound=NOW - timedelta(minutes=20))
    add_conversation(db, inbound=NOW - timedelta(minutes=5))
    add_conversation(db, inbound=NOW - timedelta(days=5))  # outside the window
    notifications.scan(db, NOW)
    item = active(db, "inbox.overdue")
    assert item.title == "2 conversaciones pendientes de responder"
    assert "40 min" in item.body and "#1, #2." in item.body
    assert item.link == "/inbox?status=pending&from=2026-11-17"

    for conv in db.scalars(select(Conversation)).all():
        conv.status = "sent"
    db.commit()
    notifications.scan(db, NOW)
    assert active(db, "inbox.overdue") is None


def add_booking(db, created_at=NOW):
    conv, message = add_conversation(db, kind="booking")
    task = BookingTask(conversation_id=conv.id, message_id=message.id, created_at=created_at)
    db.add(task)
    db.commit()
    return task


def open_bookings(db):
    return db.scalars(
        select(Notification).where(
            Notification.kind == "booking.created", Notification.resolved_at.is_(None)
        )
    ).all()


def test_scan_groups_open_bookings_in_one_notification(db):
    first = add_booking(db)
    second = add_booking(db)
    add_booking(db, created_at=NOW - timedelta(days=10))  # outside the window
    notifications.scan(db, NOW)
    [item] = open_bookings(db)
    assert item.title == "2 prerreservas pendientes de revisar"
    assert item.link == "/bookings" and item.dedupe_key == f"booking.open:{second.id}"

    second.status = "closed"
    db.commit()
    notifications.scan(db, NOW)
    [same] = open_bookings(db)
    assert same.id == item.id and same.title == "1 prerreserva pendiente de revisar"

    first.status = "closed"
    db.commit()
    notifications.scan(db, NOW)
    assert open_bookings(db) == [] and item.status == "resolved"


def test_new_booking_reopens_a_dismissed_group(db):
    add_booking(db)
    notifications.scan(db, NOW)
    [item] = open_bookings(db)
    notifications.dismiss(db, add_user(db), item)
    notifications.scan(db, NOW)
    assert open_bookings(db)[0].status == "dismissed"

    newest = add_booking(db)
    notifications.scan(db, NOW)
    [fresh] = open_bookings(db)
    assert fresh.status == "open" and fresh.dedupe_key == f"booking.open:{newest.id}"
    assert fresh.title.startswith("2 prerreservas")


def test_scan_follows_registration_alerts(db):
    conv, message = add_conversation(db)
    check = RegistrationCheck(
        conversation_id=conv.id,
        message_id=message.id,
        received_at=NOW - timedelta(hours=2),
        due_at=NOW - timedelta(hours=1),
        status="alerted",
    )
    db.add(check)
    db.commit()
    notifications.scan(db, NOW)
    assert active(db, f"registration.unregistered:{check.id}") is not None
    check.status = "registered"
    db.commit()
    notifications.scan(db, NOW)
    assert active(db, f"registration.unregistered:{check.id}") is None


def test_scan_reports_avantio_failures_and_staleness(db):
    state = ReservationSyncState(
        key="avantio_reservations",
        status="failed",
        last_error="ExportError: no hay sesión de Chrome",
        last_success_at=NOW - timedelta(hours=13),
        interval_hours=6,
    )
    db.add(state)
    db.commit()
    notifications.scan(db, NOW)
    assert "Chrome" in active(db, "avantio.sync_failed").body
    assert active(db, "avantio.sync_stale").title.startswith("Las reservas de Avantio llevan 13 h")

    state.status = "succeeded"
    state.last_success_at = NOW
    db.commit()
    notifications.scan(db, NOW)
    assert active(db, "avantio.sync_failed") is None
    assert active(db, "avantio.sync_stale") is None


def test_scan_reports_imap_errors_and_silence(db, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "imap_host", "imap.example.test")
    cursor = MailCursor(
        mailbox=settings.imap_mailbox,
        uidvalidity=1,
        last_success_at=NOW - timedelta(minutes=5),
        last_error="TimeoutError",
    )
    db.add(cursor)
    db.commit()
    notifications.scan(db, NOW)
    assert "TimeoutError" in active(db, "mail.imap").body

    cursor.last_error = None
    db.commit()
    notifications.scan(db, NOW)
    assert active(db, "mail.imap") is None

    notifications.scan(db, NOW + timedelta(hours=1))
    assert "65 min" in active(db, "mail.imap").title


def test_scan_reports_stale_availability_csv(db):
    batch = ImportBatch(
        dataset="availability",
        source="upload",
        filename="a.csv",
        checksum="x",
        status="valid",
        created_at=NOW - timedelta(hours=10),
    )
    db.add(batch)
    db.commit()
    notifications.scan(db, NOW)
    assert active(db, "availability.csv_stale").title.startswith("La disponibilidad tiene 10 h")


def test_invalid_imports_are_notified_once(db):
    batch = ImportBatch(
        dataset="reservations",
        source="upload",
        filename="r.csv",
        checksum="x",
        status="invalid",
        error="Falta la columna Localizador",
        created_at=NOW - timedelta(hours=1),
    )
    db.add(batch)
    db.commit()
    notifications.scan(db, NOW)
    item = active(db, f"import.invalid:{batch.id}")
    assert item.title == "Importación de reservas rechazada"
    notifications.dismiss(db, add_user(db, role="admin"), item)
    notifications.clear_condition(db, f"import.invalid:{batch.id}", NOW)
    notifications.scan(db, NOW)
    assert db.query(Notification).count() == 1


def test_purge_removes_old_closed_notifications(db):
    user = add_user(db)
    old = notifications.raise_condition(db, "inbox.overdue", "old", "x", at=NOW)
    db.commit()
    notifications.mark_read(db, user)
    notifications.clear_condition(db, "old", NOW - timedelta(days=200))
    kept = notifications.raise_condition(db, "inbox.overdue", "kept", "x", at=NOW)
    db.commit()
    assert notifications.purge_old(db, NOW) == 1
    db.commit()
    assert db.get(Notification, old.id) is None and db.get(Notification, kept.id) is not None
    assert db.query(NotificationRead).count() == 0


def test_one_failing_watcher_does_not_block_others(db, monkeypatch):
    def broken(db, at):
        raise RuntimeError("boom")

    monkeypatch.setattr(notifications, "WATCHERS", (broken, *notifications.WATCHERS))
    conv, _ = add_conversation(db, status="delivery_unknown")
    notifications.scan(db, NOW)
    assert active(db, f"delivery.unknown:{conv.id}") is not None


@pytest.fixture
def client(db, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_secret", "unit-test-secret-with-more-than-32-characters")
    for username, role in (("operator", "operator"), ("admin", "admin")):
        db.add(
            User(username=username, password_hash=hasher.hash("very-long-test-password"), role=role)
        )
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as test_client:

            def headers(username):
                login = test_client.post(
                    "/api/login",
                    json={"username": username, "password": "very-long-test-password"},
                )
                return {"Authorization": f"Bearer {login.json()['token']}"}

            yield test_client, headers
    finally:
        app.dependency_overrides.clear()


def test_api_lists_reads_and_dismisses(client, db):
    test_client, headers = client
    operator, admin = headers("operator"), headers("admin")
    notifications.raise_condition(db, "inbox.overdue", "inbox.overdue", "Pendientes", at=NOW)
    system = notifications.raise_condition(db, "mail.imap_failed", "mail.imap", "Correo", at=NOW)
    db.commit()

    summary = test_client.get("/api/summary", headers=operator).json()
    assert summary["notifications"]["unread"] == 1
    listing = test_client.get("/api/notifications", headers=admin).json()
    assert [item["kind"] for item in listing["items"]] == ["mail.imap_failed", "inbox.overdue"]
    assert listing["counts"]["unread"] == 2

    response = test_client.post("/api/notifications/read", json={}, headers=operator)
    assert response.json()["counts"]["unread"] == 0
    assert test_client.get("/api/notifications", headers=admin).json()["counts"]["unread"] == 2

    assert (
        test_client.post(f"/api/notifications/{system.id}/dismiss", headers=operator).status_code
        == 404
    )
    dismissed = test_client.post(f"/api/notifications/{system.id}/dismiss", headers=admin)
    assert dismissed.json()["status"] == "dismissed"
    history = test_client.get("/api/notifications?status=all", headers=admin).json()["items"]
    assert {item["status"] for item in history} == {"open", "dismissed"}
