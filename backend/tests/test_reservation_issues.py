from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import notifications, reservation_issues
from app.auth import hasher
from app.config import get_settings
from app.db import get_db
from app.main import app
from app.models import (
    ImportBatch,
    Notification,
    PropertySnapshot,
    ReservationSnapshot,
    User,
)

TODAY = date(2026, 11, 19)
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


def batch(db, dataset, warnings=None):
    item = ImportBatch(
        dataset=dataset,
        source="upload",
        filename=f"{dataset}.csv",
        checksum=f"{dataset}-{db.query(ImportBatch).count()}",
        status="valid",
        warnings=warnings or [],
        created_at=NOW,
    )
    db.add(item)
    db.commit()
    return item


def prop(db, batch_id, property_id, name, active=True):
    db.add(
        PropertySnapshot(
            batch_id=batch_id,
            source_row=1,
            row_hash=property_id,
            property_id=property_id,
            name=name,
            status_original="Activo" if active else "Inactivo",
            active=active,
            category="Apartamento",
            capacity=4,
        )
    )


def res(db, batch_id, locator, property_id, start, end, status="confirmed", **amounts):
    total = amounts.get("total", Decimal("100"))
    db.add(
        ReservationSnapshot(
            batch_id=batch_id,
            source_row=1,
            row_hash=locator,
            locator=locator,
            property_id=property_id,
            status_original=status,
            status=status,
            start_date=start,
            end_date=end,
            nights=(end - start).days,
            adults=2,
            children=0,
            infants=0,
            display_name=f"Huésped {locator}",
            portal="Booking",
            total=total,
            net=amounts.get("net", total - Decimal("10")),
            tax=amounts.get("tax", Decimal("10")),
            paid=amounts.get("paid", total),
            pending=amounts.get("pending", Decimal("0")),
        )
    )


@pytest.fixture
def data(db):
    properties = batch(db, "properties")
    prop(db, properties.id, "A", "Estrella 2")
    prop(db, properties.id, "B", "Avenida 3", active=False)
    reservations = batch(db, "reservations")
    rid = reservations.id
    # Current overlap in A, and a past one in A.
    res(db, rid, "R1", "A", date(2026, 12, 1), date(2026, 12, 8))
    res(db, rid, "R2", "A", date(2026, 12, 5), date(2026, 12, 10), status="paid")
    res(db, rid, "OLD1", "A", date(2020, 1, 1), date(2020, 1, 10))
    res(db, rid, "OLD2", "A", date(2020, 1, 5), date(2020, 1, 12))
    # Amounts: future overpaid, past underpaid with tax mismatch.
    res(db, rid, "M1", "A", date(2027, 3, 1), date(2027, 3, 5), paid=Decimal("150"))
    res(
        db, rid, "M2", "A", date(2025, 3, 1), date(2025, 3, 5), paid=Decimal("40"), tax=Decimal("0")
    )
    # Future stay in inactive B; past stay there is fine; unknown apartment C.
    res(db, rid, "I1", "B", date(2027, 1, 1), date(2027, 1, 4))
    res(db, rid, "I2", "B", date(2024, 1, 1), date(2024, 1, 4))
    res(db, rid, "U1", "C", date(2027, 2, 1), date(2027, 2, 4))
    db.commit()
    return db


def keys(items):
    return [item["key"] for item in items]


def test_collect_groups_incidents_with_current_flags(data):
    result = reservation_issues.collect(data, TODAY)
    overlap = {item["key"]: item for item in result["overlap"]}
    current = overlap["overlap:R1|R2"]
    assert current["current"] and current["nights"] == 3
    assert current["overlap_start"] == date(2026, 12, 5)
    assert current["property"]["name"] == "Estrella 2"
    assert [r["locator"] for r in current["reservations"]] == ["R1", "R2"]
    assert overlap["overlap:OLD1|OLD2"]["current"] is False
    assert keys(result["overlap"])[0] == "overlap:R1|R2"  # current first

    amounts = {item["reservation"]["locator"]: item for item in result["amounts"]}
    assert amounts["M1"]["balance_difference"] == 50 and amounts["M1"]["current"]
    assert amounts["M2"]["balance_difference"] == -60 and not amounts["M2"]["current"]
    assert amounts["M2"]["tax_difference"] == -10

    assert keys(result["inactive"]) == ["inactive:I1"]
    assert result["inactive"][0]["property"]["active"] is False
    assert keys(result["unknown"]) == ["unknown:U1"]
    assert result["unknown"][0]["property"]["known"] is False
    assert result["counts"]["overlap"] == {"total": 2, "current": 1, "pending": 1}


def test_reviews_stop_counting_and_can_be_undone(data):
    user = User(username="bea", password_hash="x")
    data.add(user)
    data.commit()
    review = reservation_issues.mark_reviewed(data, "overlap:R1|R2", user, "Bloqueo correcto")
    assert review["by"] == "bea"
    result = reservation_issues.collect(data, TODAY)
    item = next(i for i in result["overlap"] if i["key"] == "overlap:R1|R2")
    assert item["review"]["note"] == "Bloqueo correcto"
    assert result["counts"]["overlap"]["pending"] == 0

    reservation_issues.unmark_reviewed(data, "overlap:R1|R2")
    assert reservation_issues.collect(data, TODAY)["counts"]["overlap"]["pending"] == 1
    with pytest.raises(reservation_issues.ReservationIssueError):
        reservation_issues.mark_reviewed(data, "nope:1", user)


def active(db, kind):
    return db.scalars(
        select(Notification).where(Notification.kind == kind, Notification.resolved_at.is_(None))
    ).all()


def test_notifications_follow_current_unreviewed_incidents(data):
    notifications.scan(data, NOW)
    [overlap] = active(data, "reservations.overlap")
    assert overlap.title == "Posible doble reserva en Estrella 2"
    assert "R1 y R2" in overlap.body and overlap.link.endswith("issue=overlap:R1|R2")
    [amounts] = active(data, "reservations.amounts")
    assert amounts.title.startswith("1 reserva vigente o futura") and "M1." in amounts.body
    [inactive] = active(data, "reservations.inactive")
    assert "Avenida 3" in inactive.body
    assert len(active(data, "reservations.unknown")) == 1

    user = User(username="bea", password_hash="x")
    data.add(user)
    data.commit()
    reservation_issues.mark_reviewed(data, "overlap:R1|R2", user)
    notifications.scan(data, NOW)
    assert active(data, "reservations.overlap") == []
    assert overlap.status == "resolved"


@pytest.fixture
def client(db, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_secret", "unit-test-secret-with-more-than-32-characters")
    for username, role in (("bea", "operator"), ("ana", "admin")):
        db.add(
            User(username=username, password_hash=hasher.hash("very-long-test-password"), role=role)
        )
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as test_client:

            def headers(username):
                login = test_client.post(
                    "/api/login", json={"username": username, "password": "very-long-test-password"}
                )
                return {"Authorization": f"Bearer {login.json()['token']}"}

            yield test_client, headers
    finally:
        app.dependency_overrides.clear()


def test_api_lists_reviews_and_unreviews(client, data):
    test_client, headers = client
    bea = headers("bea")
    listing = test_client.get("/api/reservation-issues", headers=bea).json()
    assert listing["counts"]["amounts"]["total"] == 2

    reviewed = test_client.post(
        "/api/reservation-issues/review",
        json={"key": "amounts:M1", "note": "Cobro doble"},
        headers=bea,
    )
    assert reviewed.json()["note"] == "Cobro doble"
    bad = test_client.post("/api/reservation-issues/review", json={"key": "x:1"}, headers=bea)
    assert bad.status_code == 422
    undone = test_client.post(
        "/api/reservation-issues/unreview", json={"key": "amounts:M1"}, headers=bea
    )
    assert undone.json()["review"] is None


def test_import_detail_separates_automatic_repairs(client, db):
    test_client, headers = client
    item = batch(
        db,
        "customers",
        [{"code": "extra_separators", "customer_id": "7"}] * 3
        + [{"code": "invalid_date", "customer_id": "8", "field": "birth_date"}],
    )
    admin = headers("ana")
    detail = test_client.get(f"/api/imports/{item.id}", headers=admin).json()
    assert detail["warning_count"] == 1 and detail["repair_count"] == 3
    assert [w["code"] for w in detail["warnings"]] == ["invalid_date"]
    listing = test_client.get("/api/imports", headers=admin).json()
    row = next(i for i in listing if i["id"] == item.id)
    assert (row["warning_count"], row["repair_count"]) == (1, 3)
