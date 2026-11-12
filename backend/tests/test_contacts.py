from __future__ import annotations

from fastapi.testclient import TestClient

from app.annotations import save_annotation, summary
from app.auth import hasher
from app.config import get_settings
from app.contacts import detect_contact, new_part, normalize_phone
from app.db import get_db
from app.main import app
from app.models import Conversation, Message, User

COLLEAGUE = "Albir Confort Real Estate <customerservice@albirconfort.es>"


def test_colleague_lead_with_email_phone_and_price():
    body = (
        "Hi Are,\n\nCould you please send an offerta to the following email:\n"
        "jane.doe@example.nl <mailto:jane.doe@example.nl>  she wants to know the\n"
        "price from 04/03/27 hasta 25/03/2027.\n\nPhone number is +31 6 12345678\n\n"
        "Thank you J\n\n <https://www.albirconfort.es/>"
    )
    contact = detect_contact(channel="email", sender=COLLEAGUE, subject="Jane Doe", body=body)
    assert contact == {
        "source": "relayed",
        "relayed_by": "customerservice@albirconfort.es",
        "name": "Jane Doe",
        "emails": ["jane.doe@example.nl"],
        "phones": ["+31612345678"],
        "wants_price": True,
    }


def test_colleague_lead_with_only_availability():
    body = "Hola, cliente nuevo.\nQuiere saber si hay algo del 1 al 15 de abril.\nTel: 612 345 678"
    contact = detect_contact(channel="email", sender=COLLEAGUE, subject="Cliente", body=body)
    assert contact["source"] == "relayed"
    assert contact["emails"] == []
    assert contact["phones"] == ["+34612345678"]
    assert contact["wants_price"] is False
    assert contact["name"] is None


def test_forwarded_customer_email_takes_the_quoted_sender():
    body = (
        "Hello my friend, Algo por ti? <https://albirconfort.es/> De: Ana Pérez "
        "<ana.perez@example.com> Enviado el: martes Para: info@albirconfort.es Asunto: "
        "Consulta Buenas tardes, buscamos alojamiento para 4 personas. Precios y condiciones. "
        "Puede contactar con dpd@example.com"
    )
    contact = detect_contact(
        channel="email",
        sender="Albir Confort <info@albirconfort.es>",
        subject="RV: Consulta alquiler temporada invierno",
        body=body,
    )
    assert contact["source"] == "relayed"
    assert contact["name"] == "Ana Pérez"
    assert contact["emails"] == ["ana.perez@example.com"]
    assert contact["wants_price"] is True


def test_internal_mail_without_customer_data_is_not_a_lead():
    contact = detect_contact(
        channel="email",
        sender="Silvia <contabilidad@albirconfort.es>",
        subject="RE: ALBARANES",
        body="Recibido. De: Reservas <reservas@albirconfort.es> Asunto: ALBARANES",
    )
    assert contact["source"] == "internal"
    assert contact["emails"] == []


def test_direct_customer_ignores_quoted_history_and_noreply():
    body = (
        "Can we stay until the 20th? My mobile is +47 912 34 567\n\n"
        "On Fri, 23 Oct 2026 reservas@albirconfort.es wrote:\n"
        "> T: +34 96 686 52 02 M: +34 639 37 30 37\n> price list attached"
    )
    contact = detect_contact(
        channel="email", sender="Ola Nordmann <ola@example.no>", subject="Re: Booking", body=body
    )
    assert contact["source"] == "direct"
    assert contact["emails"] == ["ola@example.no"]
    assert contact["phones"] == ["+4791234567"]
    assert contact["wants_price"] is False
    assert new_part(body).startswith("Can we stay")
    noreply = detect_contact(
        channel="email", sender="Avantio <noreply@avantio.com>", subject="Aviso", body="Hola"
    )
    assert noreply["emails"] == []


def test_glued_header_words_and_own_numbers_are_cleaned(monkeypatch):
    monkeypatch.setattr(get_settings(), "internal_phone_numbers", "+34 966 865 202")
    contact = detect_contact(
        channel="email",
        sender=COLLEAGUE,
        subject="Lead",
        body="Cliente: john@example.comAsunto Llamar al 966 865 202 o al 0034 600 111 222",
    )
    assert contact["emails"] == ["john@example.com"]
    assert contact["phones"] == ["+34600111222"]


def test_dates_and_reference_numbers_are_not_phones():
    contact = detect_contact(
        channel="email",
        sender=COLLEAGUE,
        subject="Lead",
        body="Fechas 04/03/2027 - 25/03/2027, reserva 5010180697, código 130680. a@b.es",
    )
    assert contact["phones"] == []
    assert normalize_phone("612 345 678") == "+34612345678"
    assert normalize_phone("123") is None


def test_api_shows_the_customer_contact_and_annotation_can_confirm_it(db, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_secret", "unit-test-secret-with-more-than-32-characters")
    db.add(User(username="operator", password_hash=hasher.hash("very-long-test-password"),
                role="operator"))
    conversation = Conversation(channel="email", external_key="<lead@x>", kind="inquiry",
                                customer_address=COLLEAGUE)
    db.add(conversation)
    db.flush()
    message = Message(conversation_id=conversation.id, channel="email", provider_id="<lead@x>",
                      direction="inbound", sender=COLLEAGUE, subject="Jane Doe",
                      body="Wants an oferta 01/04/2027 until 15/05/2027. Email: jane@example.nl",
                      classification_label="demand", classification_confidence="high")
    db.add(message)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            token = client.post("/api/login", json={
                "username": "operator", "password": "very-long-test-password"}).json()["token"]
            headers = {"Authorization": f"Bearer {token}"}
            detail = client.get(f"/api/conversations/{conversation.id}", headers=headers).json()
            assert detail["contact"]["source"] == "relayed"
            assert detail["contact"]["emails"] == ["jane@example.nl"]
            assert detail["contact"]["verified"] is False
            inbox = client.get("/api/inbox?grouping=thread", headers=headers).json()
            assert inbox["items"][0]["contact"]["emails"] == ["jane@example.nl"]

            saved = client.put(
                f"/api/messages/{message.id}/annotation",
                headers=headers,
                json={"intent": "availability_request", "expected": {
                    "arrival": "2027-04-01", "departure": "2027-05-15",
                    "contact_email": "Jane@Example.nl", "contact_phone": "+31 6 12345678",
                    "wants_price": True}},
            )
            assert saved.status_code == 200
            contact = saved.json()["contact"]
            assert contact["verified"] is True
            assert contact["phones"] == ["+31612345678"]
            assert client.put(
                f"/api/messages/{message.id}/annotation", headers=headers,
                json={"intent": "availability_request",
                      "expected": {"contact_email": "not-an-email"}},
            ).status_code == 422
    finally:
        app.dependency_overrides.clear()

    extraction = summary(db)["stored_model"]["extraction"]
    assert extraction["contact_email"] == {"total": 1, "correct": 1}
    assert extraction["contact_phone"] == {"total": 1, "correct": 0}
    assert extraction["wants_price"] == {"total": 1, "correct": 1}


def test_annotation_without_contact_fields_still_validates(db):
    conversation = Conversation(channel="email", external_key="<a@x>", kind="inquiry")
    db.add(conversation)
    db.flush()
    message = Message(conversation_id=conversation.id, channel="email", provider_id="<a@x>",
                      direction="inbound", sender="x@example.com", body="hola")
    db.add(message)
    db.flush()
    annotation = save_annotation(db, message, intent="availability_request", expected={},
                                 note=None, user_id=None)
    assert annotation.expected["contact_email"] is None
    assert annotation.expected["wants_price"] is None
