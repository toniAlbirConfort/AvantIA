from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app import booking_pace
from app.auth import hasher
from app.booking_pace import ALL_GROUPS, booking_pace_view
from app.config import get_settings
from app.db import get_db
from app.main import app
from app.models import ImportBatch, PropertySnapshot, ReservationSnapshot, TariffSeason, User
from app.tariff_lab import PropertyGroup

GROUP = "Grupo playa"
TODAY = date(2026, 11, 20)


@pytest.fixture(autouse=True)
def _fixed_groups(monkeypatch):
    monkeypatch.setattr(
        booking_pace,
        "_property_mappings",
        lambda db: {
            unit: PropertyGroup(property_id=unit, tariff_name=GROUP) for unit in ("A1", "A2")
        },
    )


def _seed(db) -> None:
    created = datetime(2026, 11, 19, tzinfo=UTC)
    properties = ImportBatch(
        dataset="properties", source="t", filename="p", checksum="p", status="valid",
        row_count=2, created_at=created,
    )
    reservations = ImportBatch(
        dataset="reservations", source="t", filename="r", checksum="r", status="valid",
        row_count=1, created_at=created,
    )
    db.add_all([properties, reservations])
    db.flush()
    for index, unit in enumerate(("A1", "A2")):
        db.add(PropertySnapshot(
            batch_id=properties.id, source_row=index, row_hash=unit, property_id=unit,
            name=unit, status_original="Activado", active=True, category="Apartamento",
            capacity=4, building="Edificio", city="Albir", address=None,
        ))
    db.add(TariffSeason(
        tariff_name=GROUP, rate_plan="SA", start_date=date(2027, 1, 1),
        end_date=date(2027, 12, 31), nightly_rate=Decimal("150.00"),
    ))
    counter = iter(range(1000))

    def stay(unit, start, nights, nightly, created_on, status="paid"):
        locator = f"R{next(counter)}"
        db.add(ReservationSnapshot(
            batch_id=reservations.id, source_row=1, row_hash=locator, locator=locator,
            property_id=unit, status_original=status, status=status, created_on=created_on,
            start_date=start, end_date=start + timedelta(days=nights), nights=nights,
            adults=2, children=0, infants=0, display_name="Cliente", portal=None,
            total=Decimal(nightly) * nights, net=Decimal(nightly) * nights, tax=Decimal(0),
            paid=Decimal(0), pending=Decimal(0),
        ))

    # ISO week 20 fully sold in 2023-2025, empty in 2026: a 100-point drop.
    for year in (2023, 2024, 2025):
        monday = date.fromisocalendar(year, 20, 1)
        for unit in ("A1", "A2"):
            stay(unit, monday, 7, "100", monday - timedelta(days=60))
    # ISO week 30 sold in every year, also in 2026: no drop.
    for year in (2023, 2024, 2025, 2026):
        monday = date.fromisocalendar(year, 30, 1)
        for unit in ("A1", "A2"):
            stay(unit, monday, 7, str(80 + (year - 2023) * 10), monday - timedelta(days=60))
    # Pace: March 2026 had 40 nights booked by 20/11/2025; March 2027 has 10 today.
    stay("A1", date(2026, 3, 1), 20, "100", date(2025, 10, 1))
    stay("A2", date(2026, 3, 1), 20, "100", date(2025, 10, 1))
    stay("A1", date(2027, 3, 1), 10, "100", date(2026, 10, 1))
    # Booked after the cut-off of the previous year: does not count for the comparison.
    stay("A2", date(2026, 3, 25), 5, "100", date(2026, 2, 1))
    # Owner use is not sellable capacity and cancelled stays do not count.
    stay("A2", date.fromisocalendar(2026, 40, 1), 7, "0", None, status="owner")
    stay("A1", date(2027, 3, 15), 5, "100", date(2026, 10, 1), status="cancelled")
    db.commit()


def test_booking_pace_finds_drops_pace_and_strategy(db):
    _seed(db)
    view = booking_pace_view(db, today=TODAY)

    assert view["year"] == 2026 and view["next_year"] == 2027
    assert view["comparison_years"] == [2023, 2024, 2025]
    group = view["groups"][GROUP]
    weeks = {item["week"]: item for item in group["weeks"]}
    assert weeks[20]["occupancy"] == 0 and weeks[20]["average"] == 1
    assert weeks[20]["drop"] == 1 and weeks[20]["offer"]["discount"] == 0.10
    assert weeks[20]["sa_next"] == "150.00" and weeks[20]["offer"]["rate"] == "135.00"
    assert weeks[30]["drop"] is None and weeks[30]["occupancy"] == 1
    assert weeks[52]["occupancy"] is None  # not finished yet
    march = group["pace"][2]
    assert (march["booked"], march["booked_previous"]) == (10, 40)
    assert march["change"] == -0.75 and march["status"] == "behind"
    assert march["final_previous"] == 45
    assert group["pace"][0]["status"] == "little_reference"
    assert group["sa_next_average"] == "150.00"
    assert group["projected_average"] is None
    assert view["groups"][ALL_GROUPS]["units"] == 2
    assert ALL_GROUPS in view["groups"] and "growth_4y" not in view["groups"][ALL_GROUPS]


def test_booking_pace_threshold_and_offer_rules(db):
    _seed(db)
    strict = booking_pace_view(db, today=TODAY, threshold=0.5)
    drops = [item["week"] for item in strict["groups"][GROUP]["weeks"] if item["drop"]]
    assert drops == [20]
    assert booking_pace._offer(0.16)["discount"] == 0.05
    assert booking_pace._offer(0.25)["discount"] == 0.07
    assert booking_pace._offer(0.31)["discount"] == 0.10


def test_booking_pace_api_requires_login(db, monkeypatch):
    _seed(db)
    settings = get_settings()
    monkeypatch.setattr(settings, "app_secret", "unit-test-secret-with-more-than-32-characters")
    db.add(User(
        username="pace-op", password_hash=hasher.hash("very-long-password"), role="operator"
    ))
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            assert client.get("/api/tariff-lab/booking-pace").status_code == 401
            token = client.post(
                "/api/login", json={"username": "pace-op", "password": "very-long-password"}
            ).json()["token"]
            headers = {"Authorization": f"Bearer {token}"}
            response = client.get("/api/tariff-lab/booking-pace?threshold=0.2", headers=headers)
            invalid = client.get("/api/tariff-lab/booking-pace?threshold=2", headers=headers)
        assert response.status_code == 200
        assert response.json()["threshold"] == 0.2
        assert invalid.status_code == 422
    finally:
        app.dependency_overrides.clear()
