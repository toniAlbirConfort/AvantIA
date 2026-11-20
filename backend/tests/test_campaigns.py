from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import hasher
from app.campaigns import is_ota_email, normalize_language, render
from app.config import get_settings
from app.db import get_db
from app.main import app
from app.models import (
    Customer,
    ImportBatch,
    MailingCampaign,
    MailingRecipient,
    MailingSuppression,
    ReservationSnapshot,
    User,
)

FILTERS = {"target_year": 2026, "base_years": [2023, 2024, 2025]}


def add_customer(db, avantio_id, first, last, **extra):
    db.add(Customer(
        avantio_id=avantio_id, batch_id=1, source_row=3, row_hash="h",
        display_name=f"{first} {last}", sort_name=f"{first} {last}".lower(),
        search_text="", first_name=first, last_name=last, **extra,
    ))


@pytest.fixture
def data(db):
    db.add(ImportBatch(dataset="customers", source="upload", filename="c.csv",
                       checksum="cust", status="valid"))
    db.flush()
    add_customer(db, "1", "Ana", "García", email="Ana@Example.test", email_opt_in=True,
                 language="Español")
    add_customer(db, "2", "Jan", "de Vries", email="jan@example.test", email_opt_in=True,
                 language="Neerlandés")
    add_customer(db, "3", "Tom", "Smith", email="tom@example.test", email_opt_in=False)
    add_customer(db, "4", "Mar", "Sol", email="mar1@example.test", email_opt_in=True)
    add_customer(db, "5", "Mar", "Sol", email="mar2@example.test", email_opt_in=True)
    add_customer(db, "6", "Eva", "Ota", email="x1@guest.booking.com", email_opt_in=True)
    add_customer(db, "7", "Ola", "Nord", email="ola@example.test", email_opt_in=True,
                 language="Sueco")
    batch = ImportBatch(dataset="reservations", source="upload", filename="r.csv",
                        checksum="res", status="valid")
    db.add(batch)
    db.flush()
    names = ["Ana García", "Ana García", "Jan de Vries", "Tom Smith", "Mar Sol", "Eva Ota",
             "Ola Nord", "Nadie Conocido"]
    for index, name in enumerate(names):
        db.add(ReservationSnapshot(
            batch_id=batch.id, source_row=index + 3, row_hash="h", locator=f"L-{index}",
            property_id="P1", status_original="paid", status="paid",
            created_on=date(2023 + index % 2, 1, 10),
            start_date=date(2023 + index % 2, 8, 1), end_date=date(2023 + index % 2, 8, 8),
            nights=7, adults=2, children=0, infants=0, display_name=name,
            portal="Albir Confort", total=700, net=630, tax=70, paid=700, pending=0,
        ))
    db.commit()
    return db


@pytest.fixture
def client(data, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_secret", "unit-test-secret-with-more-than-32-characters")
    data.add(User(username="operator", password_hash=hasher.hash("very-long-test-password"),
                  role="operator"))
    data.commit()
    app.dependency_overrides[get_db] = lambda: data
    try:
        with TestClient(app) as test_client:
            token = test_client.post("/api/login", json={
                "username": "operator", "password": "very-long-test-password",
            }).json()["token"]
            test_client.headers["Authorization"] = f"Bearer {token}"
            yield test_client
    finally:
        app.dependency_overrides.clear()


def test_language_and_ota_helpers():
    assert normalize_language("Español") == "es"
    assert normalize_language("nl-BE") == "nl"
    assert normalize_language("Noruego") == "nb"
    assert normalize_language("Sueco") is None
    assert is_ota_email("x@guest.booking.com")
    assert not is_ota_email("ana@example.test")


def test_create_campaign_freezes_audience_with_reasons(client, data):
    response = client.post("/api/campaigns", json={"filters": FILTERS})
    assert response.status_code == 200, response.text
    body = response.json()
    audience = body["audience"]
    assert audience["candidates"] == 7
    assert audience["included"] == 3  # Ana, Jan and Ola
    reasons = {item["reason"]: item["count"] for item in audience["reasons"]}
    assert reasons == {"no_match": 1, "ambiguous": 1, "ota_email": 1, "no_opt_in": 1}
    rows = {
        row.display_name: row for row in data.scalars(select(MailingRecipient))
    }
    assert rows["Ana García"].email == "ana@example.test"
    assert rows["Ana García"].language == "es"
    assert rows["Ana García"].first_name == "Ana"
    assert rows["Ana García"].data["stays"] == 2
    # Unknown languages get the English text.
    assert rows["Ola Nord"].language == "en"
    assert rows["Ola Nord"].language_raw == "Sueco"


def test_min_stays_is_the_operators_choice(client):
    body = client.post(
        "/api/campaigns", json={"filters": {**FILTERS, "min_stays": 2}}
    ).json()
    assert body["audience"]["candidates"] == 1
    assert body["audience"]["included"] == 1


def test_manual_exclusion_survives_rebuild_and_suppression_applies(client, data):
    campaign = client.post("/api/campaigns", json={"filters": FILTERS}).json()
    jan = data.scalar(select(MailingRecipient).where(
        MailingRecipient.display_name == "Jan de Vries"
    ))
    response = client.patch(
        f"/api/campaigns/{campaign['id']}/recipients/{jan.id}", json={"excluded": True}
    )
    assert response.json()["audience"]["included"] == 2
    data.add(MailingSuppression(email="ana@example.test", reason="unsubscribe"))
    data.commit()
    rebuilt = client.post(f"/api/campaigns/{campaign['id']}/audience").json()["audience"]
    reasons = {item["reason"]: item["count"] for item in rebuilt["reasons"]}
    assert reasons["manual"] == 1 and reasons["suppressed"] == 1
    assert rebuilt["included"] == 1
    # A legal exclusion cannot be undone from the screen.
    ana = data.scalar(select(MailingRecipient).where(
        MailingRecipient.display_name == "Ana García"
    ))
    assert client.patch(
        f"/api/campaigns/{campaign['id']}/recipients/{ana.id}", json={"excluded": False}
    ).status_code == 400


def test_preview_is_personalised_and_escaped(client, data):
    campaign = client.post("/api/campaigns", json={"filters": FILTERS}).json()
    ana = data.scalar(select(MailingRecipient).where(
        MailingRecipient.display_name == "Ana García"
    ))
    preview = client.get(
        f"/api/campaigns/{campaign['id']}/preview", params={"recipient_id": ana.id}
    ).json()
    assert preview["language"] == "es"
    assert preview["to"] == "ana@example.test"
    assert "Hola, Ana:" in preview["html"]
    assert "agosto de 2024" in preview["text"] or "agosto de 2023" in preview["text"]
    assert "VUELVE2026" in preview["html"]
    assert f"/api/public/campaigns/unsubscribe/{ana.token}" in preview["html"]
    assert not preview["warnings"]

    content = campaign["content"]
    content["es"]["body"] = "<script>alert(1)</script> {desconocida}"
    draft = client.post(
        f"/api/campaigns/{campaign['id']}/preview",
        json={"content": content, "offer": campaign["offer"], "recipient_id": ana.id},
    ).json()
    assert "<script>" not in draft["html"]
    assert any("{desconocida}" in item for item in draft["warnings"])


def test_language_without_text_falls_back_to_english(data):
    campaign = MailingCampaign(
        name="x", filters=FILTERS, offer={"code": "", "valid_until": "", "cta_url": ""},
        content={"en": {"subject": "Hi", "preheader": "", "headline": "Back",
                        "body": "Text", "offer": "", "cta_label": ""}},
    )
    recipient = MailingRecipient(
        campaign_id=1, guest_key="k", display_name="Jan", first_name="Jan",
        email="jan@example.test", language="nl", data={}, token="t",
    )
    preview = render(campaign, language="nl", recipient=recipient)
    assert preview["language"] == "en"
    assert any("inglés" in item for item in preview["warnings"])


def test_update_validates_content_and_offer(client):
    campaign = client.post("/api/campaigns", json={"filters": FILTERS}).json()
    url = f"/api/campaigns/{campaign['id']}"
    assert client.patch(url, json={"offer": {"cta_url": "http://insecure"}}).status_code == 400
    assert client.patch(url, json={"content": {"es": campaign["content"]["es"]}}).status_code == 400
    ok = client.patch(url, json={"name": "Verano", "offer": {
        "code": "verano26", "valid_until": "2026-03-31", "cta_url": "https://example.test/",
    }}).json()
    assert ok["name"] == "Verano" and ok["offer"]["code"] == "VERANO26"


def test_unsubscribe_link_suppresses_the_address(client, data):
    campaign = client.post("/api/campaigns", json={"filters": FILTERS}).json()
    ana = data.scalar(select(MailingRecipient).where(
        MailingRecipient.display_name == "Ana García"
    ))
    client.headers.pop("Authorization")
    page = client.get(f"/api/public/campaigns/unsubscribe/{ana.token}")
    assert page.status_code == 200 and "Confirmar la baja" in page.text
    done = client.post(f"/api/public/campaigns/unsubscribe/{ana.token}")
    assert done.status_code == 200
    assert data.scalar(select(MailingSuppression.email)) == "ana@example.test"
    assert client.get("/api/public/campaigns/unsubscribe/nope").status_code == 404
    assert campaign["id"]
