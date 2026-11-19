from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import hasher
from app.config import get_settings
from app.db import get_db
from app.main import app
from app.models import AuditEvent, Customer, ImportBatch, ReservationSnapshot, User
from app.retention import retention_csv, retention_view

TARGET = 2026


def add_customer(db, avantio_id, first, last, **extra):
    db.add(Customer(
        avantio_id=avantio_id, batch_id=1, source_row=3, row_hash="h",
        display_name=f"{first} {last}", sort_name=f"{first} {last}".lower(),
        search_text="", first_name=first, last_name=last, **extra,
    ))


def stays(db, *rows):
    batch = ImportBatch(dataset="reservations", source="upload", filename="r.csv",
                        checksum=f"c{db.query(ImportBatch).count()}", status="valid")
    db.add(batch)
    db.flush()
    for index, (name, start, status, portal, *created) in enumerate(rows):
        db.add(ReservationSnapshot(
            batch_id=batch.id, source_row=index + 3, row_hash="h", locator=f"L-{index}",
            property_id="P1", status_original=status, status=status,
            created_on=created[0] if created else date(start.year, 1, 10),
            start_date=start, end_date=date(start.year, start.month, start.day + 4), nights=4,
            adults=2, children=0, infants=0, display_name=name, portal=portal,
            total=400, net=360, tax=40, paid=400, pending=0,
        ))
    db.commit()


@pytest.fixture
def data(db):
    customers = ImportBatch(dataset="customers", source="upload", filename="c.csv",
                            checksum="cust", status="valid")
    db.add(customers)
    db.flush()
    add_customer(db, "1", "Ana", "García", email="ana@example.test", email_opt_in=True)
    add_customer(db, "2", "Luis", "Pérez", phone="+34 600000002", email_opt_in=False)
    add_customer(db, "3", "Mar", "Sol", email="mar1@example.test")
    add_customer(db, "4", "Mar", "Sol", email="mar2@example.test")
    stays(
        db,
        # Ana: two past stays, nothing this year -> to win back, linked to one customer.
        ("ANA GARCÍA", date(2023, 7, 1), "paid", "Albir Confort"),
        ("Ana Garcia", date(2025, 8, 1), "confirmed", "Albir Confort"),
        # Luis: stayed in 2024 and already has a pre-reservation for this summer.
        ("Luis Pérez", date(2024, 7, 1), "paid", "Booking.com"),
        ("Luis Pérez", date(TARGET, 7, 1), "pre_reservation", "Booking.com"),
        # Mar Sol: two customers share the name -> ambiguous link.
        ("Mar Sol", date(2024, 9, 1), "paid", "Airbnb"),
        # Eva: booked in January for a 2027 stay -> already returning.
        ("Eva Ruiz", date(2025, 5, 1), "paid", "Albir Confort"),
        ("Eva Ruiz", date(2027, 5, 1), "confirmed", "Albir Confort", date(TARGET, 1, 5)),
        # Cancelled and too-old stays never count.
        ("Pablo Cancelado", date(2025, 6, 1), "cancelled", "Albir Confort"),
        ("Rosa Antigua", date(2021, 6, 1), "paid", "Albir Confort"),
        # Cancelling a 2026 stay does not make a guest "returning".
        ("Toni Vuelve", date(2025, 3, 1), "paid", "Expedia"),
        ("Toni Vuelve", date(TARGET, 3, 1), "cancelled", "Expedia"),
        ("No disponible", date(2025, 3, 1), "paid", None),
    )
    return db


def test_lists_past_guests_without_a_booking_this_year(data):
    result = retention_view(data, target_year=TARGET)
    assert result["base_years"] == [2023, 2024, 2025]
    by_name = {item["key"]: item for item in result["items"]}
    assert set(by_name) == {"ana garcia", "mar sol", "toni vuelve"}

    ana = by_name["ana garcia"]
    assert ana["stays"] == 2 and ana["years"] == [2023, 2025] and ana["nights"] == 8
    assert ana["match"] == "exact" and ana["email"] == "ana@example.test"
    assert ana["channel"] == "Directo" and ana["last_stay_start"] == date(2025, 8, 1)
    assert [(stay["start_date"], stay["status"], stay["nights"]) for stay in ana["history"]] == [
        (date(2025, 8, 1), "confirmed", 4), (date(2023, 7, 1), "paid", 4),
    ]
    assert ana["history"][0]["guests"] == 2 and ana["history"][0]["channel"] == "Directo"
    assert result["items"][0]["key"] == "ana garcia"  # most stays first

    assert by_name["mar sol"]["match"] == "ambiguous"
    assert by_name["mar sol"]["candidates"] == 2 and by_name["mar sol"]["email"] is None
    assert by_name["toni vuelve"]["match"] == "none"
    assert result["summary"] == {"guests": 3, "linked": 1, "with_email": 1, "opt_in": 1}


def test_filters(data):
    def keys(**filters):
        result = retention_view(data, target_year=TARGET, **filters)
        return [item["key"] for item in result["items"]]

    assert keys(min_stays=2) == ["ana garcia"]
    assert keys(channel="Airbnb") == ["mar sol"]
    assert keys(contact="email") == ["ana garcia"]
    assert keys(contact="opt_in") == ["ana garcia"]
    assert keys(q="vuelve") == ["toni vuelve"]
    assert keys(base_years=[2021]) == ["rosa antigua"]
    with pytest.raises(ValueError):
        retention_view(data, target_year=TARGET, base_years=[TARGET])
    with pytest.raises(ValueError):
        retention_view(data, target_year=TARGET, channel="Fax")


def test_without_reservations(db):
    result = retention_view(db, target_year=TARGET)
    assert result["has_snapshot"] is False and result["items"] == []


def test_csv_is_excel_friendly(data):
    items = retention_view(data, target_year=TARGET, contact="email")["items"]
    content = retention_csv(items)
    assert content.startswith("﻿Huésped;Años;Estancias")
    assert "Ana Garcia;2023, 2025;2;8;800,00;01/08/2025;" in content
    assert content.rstrip().endswith(";ana@example.test;;;;Sí")


def test_api_and_audited_export(data, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_secret", "unit-test-secret-with-more-than-32-characters")
    data.add(User(username="operator", password_hash=hasher.hash("very-long-test-password"),
                  role="operator"))
    data.commit()
    app.dependency_overrides[get_db] = lambda: data
    try:
        with TestClient(app) as client:
            token = client.post("/api/login", json={
                "username": "operator", "password": "very-long-test-password",
            }).json()["token"]
            headers = {"Authorization": f"Bearer {token}"}
            response = client.get(
                f"/api/customers/retention?target_year={TARGET}&base_years=2024&base_years=2025",
                headers=headers,
            )
            assert response.status_code == 200
            # Ties on stays and amount: the most recent stay first.
            assert [item["key"] for item in response.json()["items"]] == [
                "ana garcia", "toni vuelve", "mar sol",
            ]
            bad = client.get(f"/api/customers/retention?target_year={TARGET}&base_years={TARGET}",
                             headers=headers)
            assert bad.status_code == 400
            export = client.get(
                f"/api/customers/retention.csv?target_year={TARGET}", headers=headers
            )
            assert export.status_code == 200
            assert "clientes-a-recuperar-2026.csv" in export.headers["content-disposition"]
            assert client.get("/api/customers/retention", headers={}).status_code == 401
    finally:
        app.dependency_overrides.clear()
    event = data.scalar(
        select(AuditEvent).where(AuditEvent.action == "customers.retention_exported")
    )
    assert event is not None and event.detail["rows"] == 3


def test_future_target_year_and_years_with_data(data):
    result = retention_view(data, target_year=2027, base_years=[2025, 2026])
    # Eva already booked 2027; a 2026 pre-reservation is not a stay yet.
    assert [item["key"] for item in result["items"]] == ["ana garcia", "toni vuelve"]
    assert result["years_available"] == [
        {"year": 2021, "reservations": 1}, {"year": 2023, "reservations": 1},
        {"year": 2024, "reservations": 2}, {"year": 2025, "reservations": 3},
        {"year": 2026, "reservations": 1}, {"year": 2027, "reservations": 1},
    ]


def test_month_range(data):
    from app.retention import month_span

    assert month_span(None, None) is None
    assert month_span(12, 2) == {12, 1, 2}
    with pytest.raises(ValueError):
        month_span(7, None)

    def keys(**filters):
        result = retention_view(data, target_year=TARGET, **filters)
        return [item["key"] for item in result["items"]]

    # July: Ana came in July 2023; Luis came in July 2024 but already booked July 2026.
    assert keys(month_from=7, month_to=7) == ["ana garcia"]
    assert keys(month_from=8, month_to=9) == ["ana garcia", "mar sol"]
    # Booking 2027 during 2026 counts for the whole year, not for May 2026.
    assert keys(month_from=5, month_to=5) == ["eva ruiz"]

    # Mar Sol booked February 2026: still missing September, and the row says so.
    batch = data.scalar(select(ImportBatch.id).where(ImportBatch.dataset == "reservations"))
    data.add(ReservationSnapshot(
        batch_id=batch, source_row=99, row_hash="h", locator="MAR-2026", property_id="P1",
        status_original="Confirmada", status="confirmed", created_on=date(TARGET, 1, 3),
        start_date=date(TARGET, 2, 1), end_date=date(TARGET, 2, 5), nights=4, adults=2,
        children=0, infants=0, display_name="Mar Sol", portal="Airbnb",
        total=400, net=360, tax=40, paid=400, pending=0,
    ))
    data.commit()
    result = retention_view(data, target_year=TARGET, month_from=9, month_to=9)
    mar = next(item for item in result["items"] if item["key"] == "mar sol")
    assert [booking["locator"] for booking in mar["target_bookings"]] == ["MAR-2026"]
    assert result["months"] == [9, 9]
    assert "mar sol" not in keys()  # for the whole year she is already back
