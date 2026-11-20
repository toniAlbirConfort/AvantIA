from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.auth import hasher
from app.config import get_settings
from app.customer_audit import booked_for_others_view, is_company
from app.db import get_db
from app.main import app
from app.models import (
    Conversation,
    Customer,
    ImportBatch,
    Message,
    RegistrationCheck,
    ReservationDetail,
    ReservationSnapshot,
    User,
)

RECEIVED = datetime(2026, 11, 2, 10, tzinfo=timezone.utc)


def add_customer(db, avantio_id, first, last, email=None, second=None):
    name = " ".join(filter(None, (first, last, second)))
    db.add(Customer(
        avantio_id=avantio_id, batch_id=1, source_row=3, row_hash="h", display_name=name,
        sort_name=name.lower(), search_text="", first_name=first, last_name=last,
        second_last_name=second, email=email,
    ))


def reservations(db, *rows):
    batch = ImportBatch(dataset="reservations", source="upload", filename="r.csv",
                        checksum="r", status="valid")
    db.add(batch)
    db.flush()
    for index, (customer_id, occupant, portal, status, emails) in enumerate(rows):
        locator = f"L-{index}"
        db.add(ReservationSnapshot(
            batch_id=batch.id, source_row=index + 3, row_hash="h", locator=locator,
            property_id="P1", status_original=status, status=status,
            created_on=date(2026, 1, 10 + index), start_date=date(2026, 6, 1 + index),
            end_date=date(2026, 6, 5 + index), nights=4, adults=2, children=0, infants=0,
            display_name=occupant, portal=portal, total=400, net=360, tax=40, paid=400,
            pending=0,
        ))
        db.add(ReservationDetail(
            locator=locator, batch_id=batch.id, customer_avantio_id=customer_id,
            customer_email=emails[0], occupant_email=emails[1],
        ))
    db.commit()


@pytest.fixture
def data(db):
    db.add(ImportBatch(dataset="customers", source="upload", filename="c.csv",
                       checksum="c", status="valid"))
    db.flush()
    add_customer(db, "1", "Marit", "Dosvik", "marit@example.test")
    add_customer(db, "2", "Ana", "López", "ana@example.test", second="Porta")
    add_customer(db, "3", "Einar", "Sør", "einar@example.test", second="Reime")
    add_customer(db, "4", "Hotelbeds Products", "SLU")
    add_customer(db, "5", "Club Deportivo", "Elemental Cricket")
    add_customer(db, "6", "Petrus", "Rommers", "petrus@example.test")
    add_customer(db, "7", "Lars", "Berg", "lars@example.test")
    reservations(
        db,
        # Marit booked for a friend and never stayed herself.
        ("1", "OEYSTEIN MELGAARD", "CANAL - EMAIL", "cancelled", ("marit@example.test", None)),
        # Ana booked for someone else but she also stays with us.
        ("2", "FRANCISCO GISBERT MARTINEZ", "CANAL - TELEFONO", "paid", ("ana@example.test", None)),
        ("2", "ANA LOPEZ PORTA", "CANAL - EMAIL", "paid", ("ana@example.test", None)),
        # Same person, surname transliterated: never "for someone else".
        ("3", "EINAR SOER REIME", "CANAL - EMAIL", "paid", ("einar@example.test", None)),
        # Agencies on portals are the intermediary, not someone booking for others.
        ("4", "LESLEY JANE LEES", "HOTELBEDS PRODUCTS,S.L.U.", "paid", (None, None)),
        # A club booking for its players, direct channel: listed apart as a company.
        ("5", "IAN ROBERT BINNS", "CANAL - EMAIL", "paid", (None, None)),
        ("5", "VARUN JAIN", "CANAL - EMAIL", "paid", (None, None)),
        # Different surname but same email: it is the customer.
        ("6", "ALDEGONDA LIJS", "CANAL - EMAIL", "paid",
         ("petrus@example.test", "PETRUS@example.test")),
        # Lars booked for his mother; he appears as occupant only through another customer.
        ("7", "INGRID HOLST", "CANAL - RECEPCION", "paid", ("lars@example.test", None)),
        (None, "LARS BERG", "CANAL - EMAIL", "inquiry", (None, "lars@example.test")),
        # Owner use never counts.
        ("1", "Uso de propietario", None, "owner", (None, None)),
    )
    return db


def test_company_names():
    assert is_company("HOTELBEDS PRODUCTS SLU")
    assert is_company("ALEXANDRA DUPOND UNIPESSOAL, LDA")
    assert is_company("CLUB DEPORTIVO ELEMENTAL CRICKET MADRID")
    assert is_company("VIAJES SIDETOURS, S.L.")
    assert is_company("AYUNTAMIENTO DE L'ALFAS DEL PI")
    assert not is_company("MARIT DOSVIK")
    assert not is_company("FRANCISCUS JACOBUS VAN DE WEERTHOF")


def test_customers_who_only_book_for_others(data):
    result = booked_for_others_view(data)
    assert result["has_snapshot"] is True
    assert [item["name"] for item in result["never_stay"]] == ["Marit Dosvik"]
    marit = result["never_stay"][0]
    assert marit["count"] == 1 and marit["reservations"][0]["occupant"] == "OEYSTEIN MELGAARD"
    assert marit["reservations"][0]["status"] == "cancelled"
    assert "_email" not in marit
    # Ana and Lars booked for others but also appear as guests.
    assert [item["name"] for item in result["also_stay"]] == ["Lars Berg", "Ana López Porta"]
    assert [item["name"] for item in result["companies"]] == ["Club Deportivo Elemental Cricket"]
    summary = result["summary"]
    assert summary["reservations"] == 10
    assert summary["for_others_direct"] == 5
    assert summary["for_others_intermediaries"] == 1
    assert summary["customers_never_stay"] == 1 and summary["single_reservation"] == 1
    assert summary["consultations"] == 0


def test_consultations_of_those_customers_are_listed(data):
    conversation = Conversation(channel="email", external_key="<c1@example.test>", subject="Hola")
    data.add(conversation)
    data.flush()
    message = Message(
        conversation_id=conversation.id, channel="email", provider_id="<m1@example.test>",
        direction="inbound", sender="Marit <MARIT@example.test>", recipient="r@example.test",
        subject="Apartment", body="Do you have something in May?", email_date=RECEIVED,
        created_at=RECEIVED,
    )
    data.add(message)
    data.flush()
    data.add(RegistrationCheck(
        conversation_id=conversation.id, message_id=message.id,
        customer_email="Marit@example.test", received_at=RECEIVED,
        due_at=RECEIVED + timedelta(hours=1), status="excluded", reason="huesped_con_reserva",
        mode="shadow",
    ))
    data.commit()
    result = booked_for_others_view(data)
    consultations = result["never_stay"][0]["consultations"]
    assert [(item["status"], item["reason"]) for item in consultations] == [
        ("excluded", "huesped_con_reserva")
    ]
    assert result["summary"]["consultations"] == 1


def test_without_reservations(db):
    result = booked_for_others_view(db)
    assert result["has_snapshot"] is False and result["never_stay"] == []


def test_api(data, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_secret", "unit-test-secret-with-more-than-32-characters")
    data.add(User(username="operator", password_hash=hasher.hash("very-long-test-password"),
                  role="operator"))
    data.commit()
    app.dependency_overrides[get_db] = lambda: data
    try:
        with TestClient(app) as client:
            assert client.get("/api/customers/audit/booked-for-others").status_code == 401
            token = client.post("/api/login", json={
                "username": "operator", "password": "very-long-test-password",
            }).json()["token"]
            response = client.get(
                "/api/customers/audit/booked-for-others",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200
            assert response.json()["summary"]["customers_never_stay"] == 1
    finally:
        app.dependency_overrides.clear()
