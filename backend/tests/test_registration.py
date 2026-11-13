"""Unregistered-consultation alert. Names and numbers are fictitious; each case mirrors a pattern
found in the November 2026 audit of the reservas@ mailbox."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import (
    AuditEvent,
    Conversation,
    ImportBatch,
    Job,
    Message,
    RegistrationCheck,
    ReservationSnapshot,
    ReservationSyncState,
)
from app.registration import office_deadline, run_registration_check, simulate, watch_sync

# Tuesday 10 November 2026, 10:00 in Madrid.
RECEIVED = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
LATER = RECEIVED + timedelta(hours=3)


@pytest.fixture(autouse=True)
def shadow(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "registration_alert_mode", "shadow")
    monkeypatch.setattr(settings, "registration_alert_delay_minutes", 60)
    monkeypatch.setattr(settings, "registration_alert_lookback_days", 14)
    monkeypatch.setattr(settings, "auto_reply_timezone", "Europe/Madrid")
    monkeypatch.setattr(settings, "auto_reply_night_start", "20:00")
    monkeypatch.setattr(settings, "auto_reply_night_end", "08:00")
    monkeypatch.setattr(settings, "internal_sender_domains", "albirconfort.es")
    monkeypatch.setattr(settings, "booking_sender_domains", "booking.com")
    monkeypatch.setattr(settings, "internal_phone_numbers", "")
    return settings


_counter = {"n": 0}


def reservations(db, *rows):
    """A valid reservations import of (locator, name, status, created, start, end) rows."""
    batch = ImportBatch(
        dataset="reservations",
        source="test",
        filename="r.csv",
        checksum=f"c{len(db.scalars(select(ImportBatch)).all())}",
        status="valid",
    )
    db.add(batch)
    db.flush()
    for index, (locator, name, status, created, start, end) in enumerate(rows):
        db.add(
            ReservationSnapshot(
                batch_id=batch.id,
                source_row=index + 2,
                row_hash=f"h{index}",
                locator=locator,
                property_id="130650",
                status_original=status,
                status=status,
                created_on=created,
                start_date=start,
                end_date=end,
                nights=(end - start).days,
                adults=2,
                children=0,
                infants=0,
                display_name=name,
                total=Decimal("100"),
                net=Decimal("90"),
                tax=Decimal("10"),
                paid=Decimal("0"),
                pending=Decimal("100"),
            )
        )
    db.commit()
    return batch


def email(
    db,
    sender,
    subject,
    body,
    *,
    at=RECEIVED,
    label="demand",
    confidence="high",
    conversation=None,
):
    _counter["n"] += 1
    n = _counter["n"]
    if conversation is None:
        conversation = Conversation(
            channel="email", external_key=f"<c{n}@example.test>", subject=subject
        )
        db.add(conversation)
        db.flush()
    message = Message(
        conversation_id=conversation.id,
        channel="email",
        provider_id=f"<m{n}@example.test>",
        direction="inbound",
        sender=sender,
        recipient="reservas@albirconfort.es",
        subject=subject,
        body=body,
        classification_label=label,
        classification_confidence=confidence,
        email_date=at,
        created_at=at,
    )
    db.add(message)
    db.commit()
    return conversation


def check_for(db, conversation):
    return db.scalar(
        select(RegistrationCheck).where(RegistrationCheck.conversation_id == conversation.id)
    )


DAY = RECEIVED.date()
NEXT_YEAR = (date(2027, 3, 1), date(2027, 3, 15))


def test_registered_despite_capitals_middle_names_and_transliteration(db):
    reservations(db, ("40000001-130650", "KARI MARIE BROENDBO", "inquiry", DAY, *NEXT_YEAR))
    conv = email(db, "Kari Brøndbo <kari@example.test>", "Ledig leilighet?", "1.-15. mars?")
    run_registration_check(db, LATER)
    check = check_for(db, conv)
    assert (check.status, check.reason, check.locator) == (
        "registered",
        "reserva",
        "40000001-130650",
    )


def test_any_status_counts_and_surname_first_is_found(db):
    reservations(db, ("40000002-130651", "MARCHETTI Lucie", "paid", DAY, *NEXT_YEAR))
    conv = email(db, "Lucie Marchetti <lm@example.test>", "Appartement", "Du 1 au 15 mars")
    run_registration_check(db, LATER)
    assert check_for(db, conv).status == "registered"


def test_surname_hidden_in_the_email_address(db):
    reservations(db, ("40000003-130652", "CAROLINA ANNE VAN DER BERGSMA", "paid", DAY, *NEXT_YEAR))
    conv = email(db, "Carla <cbergsma58@example.test>", "Request for booking in March", "Hi")
    run_registration_check(db, LATER)
    assert check_for(db, conv).status == "registered"


def test_unregistered_consultation_alerts_after_office_hour_and_resolves_later(db):
    reservations(db, ("40000004-130653", "OTRA PERSONA", "paid", DAY, *NEXT_YEAR))
    conv = email(
        db,
        "Beate Lindqvist <bl@example.test>",
        "Rental",
        "Is it possible? 1 apartment for 2 people 21.9-7.10",
    )
    run_registration_check(db, RECEIVED + timedelta(minutes=30))
    check = check_for(db, conv)
    assert check.status == "pending"
    assert check.due_at.replace(tzinfo=UTC) == RECEIVED + timedelta(hours=1)  # SQLite: naive

    run_registration_check(db, LATER)
    db.refresh(check)
    assert (check.status, check.mode) == ("alerted", "shadow")
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "registration.alert"))

    # A later sync brings the Petición: the alert closes itself with the locator.
    reservations(
        db,
        ("40000004-130653", "OTRA PERSONA", "paid", DAY, *NEXT_YEAR),
        ("40000005-130654", "BEATE LINDQVIST", "inquiry", DAY + timedelta(days=1), *NEXT_YEAR),
    )
    run_registration_check(db, LATER + timedelta(days=1))
    db.refresh(check)
    assert (check.status, check.reason) == ("registered", "registrada_tras_aviso")
    assert check.locator == "40000005-130654"


def test_alert_is_recorded_once(db):
    reservations(db, ("40000006-130655", "OTRA PERSONA", "paid", DAY, *NEXT_YEAR))
    email(db, "Svein Hagen <sh@example.test>", "En uke", "En uke fra 17 okt - 24 okt?")
    assert run_registration_check(db, LATER)["alerted"] == 1
    assert run_registration_check(db, LATER + timedelta(hours=6))["alerted"] == 0
    events = db.scalars(select(AuditEvent).where(AuditEvent.action == "registration.alert"))
    assert len(events.all()) == 1


def test_relayed_lead_from_a_colleague_is_checked_even_if_left_in_review(db):
    reservations(db, ("40000007-130656", "OTRA PERSONA", "paid", DAY, *NEXT_YEAR))
    conv = email(
        db,
        "Albir Confort <customerservice@albirconfort.es>",
        "Anke Vermeulen",
        "Could you send an offer to anke@example.test, price from 04/03/27 to 25/03/27. "
        "Phone +31600000000",
        label="uncertain",
        confidence="low",
    )
    run_registration_check(db, LATER)
    check = check_for(db, conv)
    assert check.status == "alerted"
    assert check.customer_email == "anke@example.test"


def test_existing_guest_known_from_address_history_is_not_alerted(db):
    reservations(
        db,
        (
            "40000008-130657",
            "VERNER ERIK LUND",
            "confirmed",
            DAY - timedelta(days=60),
            DAY + timedelta(days=30),
            DAY + timedelta(days=80),
        ),
    )
    email(
        db,
        "Verner Gmail <vlund@example.test>",
        "Re: Booking details 40000008-130657",
        "Bank transfer done",
        at=RECEIVED - timedelta(days=10),
    )
    conv = email(
        db,
        "Verner Gmail <vlund@example.test>",
        "Booking of the big apartment",
        "A little correction: the exact dates will be 13 december to 02 march.",
    )
    run_registration_check(db, LATER)
    assert (check_for(db, conv).status, check_for(db, conv).reason) == (
        "excluded",
        "huesped_con_reserva",
    )


def test_guest_without_display_name_matched_by_address_prefix(db):
    reservations(
        db,
        (
            "40000009-130658",
            "GUNNER THYGESEN",
            "paid",
            DAY - timedelta(days=90),
            DAY - timedelta(days=1),
            DAY + timedelta(days=10),
        ),
    )
    conv = email(db, "<gthyge@example.test>", "Apartment", "We arrive saturday 04 april 16.00")
    run_registration_check(db, LATER)
    assert check_for(db, conv).reason == "huesped_con_reserva"


def test_shared_surname_alone_does_not_make_someone_a_guest(db):
    reservations(
        db,
        (
            "40000010-130659",
            "CHRISTINE DAVIES",
            "paid",
            DAY - timedelta(days=90),
            DAY - timedelta(days=1),
            DAY + timedelta(days=10),
        ),
    )
    conv = email(
        db,
        "Chris Davies <chris@example.test>",
        "Villa Accomodation",
        "Family of 11 looking for accommodation from 24 July to 5th of August",
    )
    run_registration_check(db, LATER)
    assert check_for(db, conv).status == "alerted"


def test_asking_about_cancellations_is_still_a_consultation(db):
    reservations(db, ("40000011-130660", "OTRA PERSONA", "paid", DAY, *NEXT_YEAR))
    conv = email(
        db,
        "Gunnar Rask <gr@example.test>",
        "Apartment block A, mid October - mid November",
        "Do you have any possibility? Or tell us if you get any cancellation.",
    )
    run_registration_check(db, LATER)
    assert check_for(db, conv).status == "alerted"


@pytest.mark.parametrize(
    ("sender", "subject", "body", "label", "reason"),
    [
        (
            "Booking.com <noreply@booking.com>",
            "New booking",
            "Arrival 1 March",
            "demand",
            "plataforma_o_interno",
        ),
        ("Shop <news@shop.example>", "Offers", "Our week deals", "non_demand", "no_demanda"),
        (
            "Guest <g@example.test>",
            "Wifi",
            "Wifi does not work in the apartment",
            "guest_service",
            "atencion_huesped",
        ),
        (
            "Hans <h@example.test>",
            "Voorstel",
            "Ik heb uw voorstel samengesteld <https://files.example/doc-2026-01-14-15.html>",
            "demand",
            "no_consulta",
        ),
        (
            "Claire <c@vendor.example>",
            "Invitation: Meet with us @ Wed 21 Jan 2026",
            "Meeting 16:00 - 16:30 about rentals",
            "demand",
            "no_consulta",
        ),
        (
            "Pat <p@example.test>",
            "RE: Booking",
            "Thanks, see you 1-15 March",
            "demand",
            "respuesta",
        ),
        (
            "Wendy <w@example.test>",
            "Cancellation",
            "Please cancel our booking 1/2-1/3",
            "demand",
            "parece_huesped",
        ),
    ],
)
def test_not_new_consultations_are_excluded(db, sender, subject, body, label, reason):
    reservations(db, ("40000012-130661", "OTRA PERSONA", "paid", DAY, *NEXT_YEAR))
    conv = email(db, sender, subject, body, label=label)
    run_registration_check(db, LATER)
    check = check_for(db, conv)
    assert (check.status, check.reason) == ("excluded", reason)


def test_follow_up_from_same_customer_is_not_a_new_alert(db):
    reservations(db, ("40000013-130662", "OTRA PERSONA", "paid", DAY, *NEXT_YEAR))
    email(
        db,
        "Ana Ruiz <ana@example.test>",
        "Apartamento",
        "Del 1 al 15 de marzo",
        at=RECEIVED - timedelta(days=3),
    )
    second = email(db, "Ana Ruiz <ana@example.test>", "Apartamento marzo", "¿Del 2 al 16?")
    run_registration_check(db, LATER)
    assert check_for(db, second).reason == "seguimiento"


def test_avantio_offer_answered_by_the_customer_counts_as_registered(db):
    reservations(db, ("40000014-130663", "OTRA PERSONA", "paid", DAY, *NEXT_YEAR))
    conv = email(
        db,
        "Veerle Agency <va@example.test>",
        "Client again",
        "My client asks for a one bedroom from 25/7 to 14/8",
    )
    email(
        db,
        "Veerle Agency <va@example.test>",
        "RE: Petición información apartamento 130663",
        "Thanks!",
        at=RECEIVED + timedelta(hours=1),
    )
    run_registration_check(db, LATER)
    assert (check_for(db, conv).status, check_for(db, conv).reason) == (
        "registered",
        "plantilla_avantio",
    )


def test_waits_for_the_classifier_but_not_forever(db):
    reservations(db, ("40000015-130664", "OTRA PERSONA", "paid", DAY, *NEXT_YEAR))
    conv = email(
        db, "Ola Berg <ob@example.test>", "Leilighet", "Uke 10?", label=None, confidence=None
    )
    run_registration_check(db, RECEIVED + timedelta(minutes=10))
    assert check_for(db, conv) is None
    run_registration_check(db, LATER)
    assert check_for(db, conv).status == "alerted"


def test_off_mode_does_nothing_and_enqueues_nothing(db, shadow, monkeypatch):
    from app.planning import _after_reservations_import

    monkeypatch.setattr(shadow, "registration_alert_mode", "off")
    batch = reservations(db, ("40000016-130665", "OTRA PERSONA", "paid", DAY, *NEXT_YEAR))
    email(db, "Ola Berg <ob@example.test>", "Leilighet", "Uke 10?")
    assert run_registration_check(db, LATER)["created"] == 0
    _after_reservations_import(db, batch)
    assert db.scalar(select(Job).where(Job.kind == "registration_check")) is None


def test_valid_import_enqueues_a_check_in_shadow(db):
    from app.planning import _after_reservations_import

    batch = reservations(db, ("40000017-130666", "OTRA PERSONA", "paid", DAY, *NEXT_YEAR))
    _after_reservations_import(db, batch)
    job = db.scalar(select(Job).where(Job.kind == "registration_check"))
    assert job is not None and job.payload == {"batch_id": batch.id}


def test_no_reservations_imported_yet_means_no_decision(db):
    conv = email(db, "Ola Berg <ob@example.test>", "Leilighet", "Uke 10?")
    assert run_registration_check(db, LATER)["skipped"] == "sin_reservas"
    assert check_for(db, conv) is None


@pytest.mark.parametrize(
    ("received", "expected"),
    [
        # Tuesday 10:00 Madrid -> 11:00
        (datetime(2026, 11, 10, 9, 0, tzinfo=UTC), datetime(2026, 11, 10, 10, 0, tzinfo=UTC)),
        # Friday 19:30 Madrid: 30 min on Friday, 30 on Monday from 08:00 -> Monday 08:30
        (datetime(2026, 11, 13, 18, 30, tzinfo=UTC), datetime(2026, 11, 16, 7, 30, tzinfo=UTC)),
        # Saturday -> Monday 09:00 Madrid
        (datetime(2026, 11, 14, 11, 0, tzinfo=UTC), datetime(2026, 11, 16, 8, 0, tzinfo=UTC)),
        # Wednesday 23:00 Madrid -> Thursday 09:00
        (datetime(2026, 11, 11, 22, 0, tzinfo=UTC), datetime(2026, 11, 12, 8, 0, tzinfo=UTC)),
    ],
)
def test_office_deadline(received, expected):
    assert office_deadline(received, 60) == expected


def test_stale_sync_is_recorded_once_per_episode(db):
    db.add(
        ReservationSyncState(
            key="avantio_reservations",
            status="failed",
            last_success_at=RECEIVED - timedelta(hours=13),
            interval_hours=6,
        )
    )
    db.commit()
    assert watch_sync(db, RECEIVED) is True
    assert watch_sync(db, RECEIVED + timedelta(hours=1)) is False
    state = db.get(ReservationSyncState, "avantio_reservations")
    state.last_success_at = RECEIVED + timedelta(hours=2)
    db.commit()
    assert watch_sync(db, RECEIVED + timedelta(hours=3)) is False
    assert watch_sync(db, RECEIVED + timedelta(hours=20)) is True


def test_simulation_writes_nothing(db):
    reservations(db, ("40000018-130667", "OTRA PERSONA", "paid", DAY, *NEXT_YEAR))
    email(db, "Ola Berg <ob@example.test>", "Leilighet", "Uke 10?")
    result = simulate(db, 30, LATER)
    assert result["counts"] == {"pending": 1}
    assert db.scalar(select(RegistrationCheck)) is None


def _login(client, db, role):
    from app.auth import hasher
    from app.models import User

    db.add(User(username=role, password_hash=hasher.hash("very-long-test-password"), role=role))
    db.commit()
    login = client.post(
        "/api/login", json={"username": role, "password": "very-long-test-password"}
    )
    return {"Authorization": f"Bearer {login.json()['token']}"}


def test_operator_links_or_dismisses_alerts_from_the_api(db, shadow, monkeypatch):
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app

    monkeypatch.setattr(shadow, "app_secret", "unit-test-secret-with-more-than-32-characters")
    reservations(
        db,
        ("40000020-130650", "OTRA PERSONA", "paid", DAY, *NEXT_YEAR),
        ("40000021-130651", "PEDRO MARTI", "inquiry", DAY, *NEXT_YEAR),
    )
    first = email(db, "Laura Sanz <ls@example.test>", "Apartamento", "Del 1 al 15 de marzo")
    second = email(db, "Mark <mk@example.test>", "Offer", "Do you have 1-15 March?")
    run_registration_check(db, LATER)
    one, two = check_for(db, first), check_for(db, second)
    assert one.status == two.status == "alerted"

    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            headers = _login(client, db, "operator")
            listed = client.get("/api/inbox?registration=alerted", headers=headers).json()
            assert {item["id"] for item in listed["items"]} == {first.id, second.id}
            assert {item["registration_status"] for item in listed["items"]} == {"alerted"}

            missing = client.post(
                f"/api/registration-checks/{one.id}/link", headers=headers, json={"locator": "999"}
            )
            assert missing.status_code == 422
            linked = client.post(
                f"/api/registration-checks/{one.id}/link",
                headers=headers,
                json={"locator": "40000021"},  # the number before the hyphen is enough
            )
            assert linked.status_code == 200
            assert (linked.json()["status"], linked.json()["locator"]) == (
                "registered",
                "40000021-130651",
            )

            without_note = client.post(
                f"/api/registration-checks/{two.id}/dismiss",
                headers=headers,
                json={"reason": "otro"},
            )
            assert without_note.status_code == 422
            dismissed = client.post(
                f"/api/registration-checks/{two.id}/dismiss",
                headers=headers,
                json={"reason": "proveedor", "note": "agencia"},
            )
            assert dismissed.json()["reason"] == "operador:proveedor"

            detail = client.get(f"/api/conversations/{first.id}", headers=headers).json()
            assert detail["registration"]["reason"] == "vinculada_manual"
            summary = client.get("/api/integrations", headers=headers).json()["registration_alert"]
            assert summary["mode"] == "shadow" and summary["open_alerts"] == 0
            assert summary["last_7_days"]["linked_by_operator"] == 1
            assert summary["last_7_days"]["dismissed_by_operator"] == 1
            assert (
                client.get("/api/inbox?registration=alerted", headers=headers).json()["items"] == []
            )
    finally:
        app.dependency_overrides.clear()
    actions = [event.action for event in db.scalars(select(AuditEvent)).all()]
    assert "registration.linked" in actions and "registration.dismissed" in actions
    # A later automatic run never overrides what the operator decided.
    run_registration_check(db, LATER + timedelta(days=1))
    db.refresh(two)
    assert two.status == "excluded"
