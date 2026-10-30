from __future__ import annotations

import asyncio
import importlib.util
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.auth import hasher
from app.config import get_settings
from app.db import get_db
from app.main import app
from app.models import (
    AvailabilitySlot,
    ImportBatch,
    PropertySnapshot,
    ReservationSnapshot,
    TariffGroupAssignment,
    TariffHoliday,
    TariffSeason,
    TariffStudy,
    User,
)
from app.tariff_lab import (
    MarketObservation,
    MarketSearchRequest,
    TariffLabError,
    _base_rate,
    _historical_day_metrics,
    _holiday_matches,
    _merge_seasons,
    calculate_study,
    create_study,
    register_market_provider,
    study_report_pdf,
    tariff_readiness,
    tariffs_csv,
    validate_study_request,
)

MIGRATION_PATH = Path(__file__).parents[1] / "alembic" / "versions" / "0009_tariff_lab.py"
MIGRATION_SPEC = importlib.util.spec_from_file_location("tariff_lab_migration", MIGRATION_PATH)
assert MIGRATION_SPEC and MIGRATION_SPEC.loader
TARIFF_LAB_MIGRATION = importlib.util.module_from_spec(MIGRATION_SPEC)
MIGRATION_SPEC.loader.exec_module(TARIFF_LAB_MIGRATION)


class StubMarketProvider:
    name = "test-rate-provider"

    def __init__(self):
        self.requests: list[MarketSearchRequest] = []

    async def search(self, request: MarketSearchRequest) -> list[MarketObservation]:
        self.requests.append(request)
        return [
            MarketObservation(
                stay_date=request.start_date,
                nightly_rate=Decimal("140.00"),
                currency="EUR",
                source="authorized-api",
                comparable_name=f"Comparable {index}",
                source_url=f"https://example.test/{index}",
                captured_at=datetime(2026, 10, 30, tzinfo=UTC),
                guests=request.guests,
                nights=request.nights,
                includes_mandatory_fees=True,
                cancellation_policy="free cancellation",
            )
            for index in range(3)
        ]


def test_tariff_lab_migration_creates_mapping_holiday_and_study_tables():
    engine = create_engine("sqlite+pysqlite://")
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                TARIFF_LAB_MIGRATION.upgrade()
            tables = set(
                connection.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).scalars()
            )
        assert {"tariff_group_assignments", "tariff_holidays", "tariff_studies"} <= tables
    finally:
        engine.dispose()


def test_study_request_validates_dates_groups_and_search_context():
    validate_study_request(
        name="Temporada 2027",
        start=date(2027, 1, 1),
        end=date(2027, 12, 31),
        guests=2,
        nights=7,
        groups=["Grupo SA"],
        today=date(2026, 10, 30),
    )
    try:
        validate_study_request(
            name="",
            start=date(2026, 1, 1),
            end=date(2028, 1, 1),
            guests=0,
            nights=0,
            groups=[],
            today=date(2026, 10, 30),
        )
    except TariffLabError as exc:
        assert "nombre" in str(exc)
    else:
        raise AssertionError("invalid study request was accepted")


def test_holiday_scopes_and_sa_season_boundaries_are_inclusive():
    assignment = TariffGroupAssignment(
        property_id="P-1",
        tariff_name="Grupo SA",
        municipality="Alfàs del Pi",
        autonomous_community="Comunitat Valenciana",
    )
    day = date(2028, 3, 19)
    holidays = [
        TariffHoliday(holiday_date=day, name="Nacional", scope="national"),
        TariffHoliday(
            holiday_date=day,
            name="Autonómico",
            scope="regional",
            region="Comunitat Valenciana",
        ),
        TariffHoliday(
            holiday_date=day,
            name="Municipal",
            scope="municipal",
            municipality="Alfas del Pi",
        ),
        TariffHoliday(
            holiday_date=day,
            name="Otro municipio",
            scope="municipal",
            municipality="Benidorm",
        ),
    ]
    assert {item.name for item in _holiday_matches(day, [assignment], holidays)} == {
        "Nacional",
        "Autonómico",
        "Municipal",
    }

    rates = [
        TariffSeason(
            tariff_name="Grupo SA",
            rate_plan="SA",
            start_date=date(2027, 1, 1),
            end_date=date(2027, 1, 10),
            nightly_rate=Decimal("100"),
        ),
        TariffSeason(
            tariff_name="Grupo SA",
            rate_plan="SA",
            start_date=date(2027, 1, 11),
            end_date=date(2027, 1, 20),
            nightly_rate=Decimal("110"),
        ),
        TariffSeason(
            tariff_name="Grupo SA",
            rate_plan="SA",
            start_date=date(2027, 2, 28),
            end_date=date(2027, 3, 2),
            nightly_rate=Decimal("80"),
        ),
    ]
    assert _base_rate(rates, "Grupo SA", date(2027, 1, 10)) == Decimal("100")
    assert _base_rate(rates, "Grupo SA", date(2027, 1, 11)) == Decimal("110")
    assert _base_rate(rates, "Grupo SA", date(2028, 2, 29)) == Decimal("80")
    merged = _merge_seasons(
        [
            {"date": "2028-02-28", "tariff_name": "Grupo SA", "recommended_rate": "80"},
            {"date": "2028-02-29", "tariff_name": "Grupo SA", "recommended_rate": "80"},
            {"date": "2028-03-01", "tariff_name": "Grupo SA", "recommended_rate": "80"},
        ]
    )
    assert merged[0]["start_date"] == "2028-02-28"
    assert merged[0]["end_date"] == "2028-03-01"
    assert merged[0]["day_count"] == 3


def test_readiness_exposes_missing_history_provider_mapping_and_calendar(db):
    readiness = tariff_readiness(db)
    assert readiness["can_generate"] is False
    assert readiness["groups"] == []
    assert {item["key"] for item in readiness["checks"]} == {
        "properties",
        "group_mapping",
        "reservations",
        "availability_history",
        "market_provider",
        "holiday_calendar",
    }


def _make_study_fixture(db):
    property_batch = ImportBatch(
        dataset="properties",
        source="upload",
        filename="properties.csv",
        checksum="p",
        status="valid",
        row_count=1,
    )
    reservation_batch = ImportBatch(
        dataset="reservations",
        source="upload",
        filename="reservations.csv",
        checksum="r",
        status="valid",
        row_count=2,
    )
    db.add_all([property_batch, reservation_batch])
    db.flush()
    availability_batches = []
    for year in (2025, 2026):
        batch = ImportBatch(
            dataset="availability",
            source="upload",
            filename=f"availability-{year}.csv",
            checksum=str(year),
            status="valid",
            row_count=1,
        )
        db.add(batch)
        db.flush()
        availability_batches.append(batch)
        db.add(
            AvailabilitySlot(
                batch_id=batch.id,
                property_id="P-1",
                property_name="Alojamiento 1",
                capacity=4,
                start_date=date(year, 1, 1),
                end_date=date(year + 1, 1, 1),
                status="available",
            )
        )
    db.add(
        PropertySnapshot(
            batch_id=property_batch.id,
            source_row=3,
            row_hash="p1",
            property_id="P-1",
            name="Alojamiento 1",
            status_original="Activado",
            active=True,
            category="Apartamento",
            capacity=4,
            building="Edificio A",
            city="Alfaz del Pi",
            address=None,
        )
    )
    db.add(
        TariffGroupAssignment(
            property_id="P-1",
            tariff_name="Grupo SA",
            municipality="Alfaz del Pi",
            province="Alicante",
            autonomous_community="Comunitat Valenciana",
        )
    )
    for index, year in enumerate((2025, 2026)):
        db.add(
            ReservationSnapshot(
                batch_id=reservation_batch.id,
                source_batch_id=reservation_batch.id,
                source_row=index + 3,
                row_hash=f"r{index}",
                locator=f"R-{year}",
                property_id="P-1",
                status_original="Confirmada",
                status="confirmed",
                created_on=date(year, 1, 1),
                start_date=date(year, 1, 6),
                end_date=date(year, 1, 7),
                nights=1,
                adults=2,
                children=0,
                infants=0,
                display_name="Huésped",
                portal="Directo",
                total=Decimal("155"),
                net=Decimal("155"),
                tax=Decimal("0"),
                paid=Decimal("155"),
                pending=Decimal("0"),
            )
        )
        db.add(
            TariffHoliday(
                holiday_date=date(year, 1, 6),
                name="Epifanía",
                scope="national",
                source="test",
            )
        )
    db.add(
        TariffHoliday(
            holiday_date=date(2027, 1, 6),
            name="Epifanía",
            scope="national",
            source="test",
        )
    )
    db.add(
        TariffSeason(
            tariff_name="Grupo SA",
            rate_plan="SA",
            start_date=date(2027, 1, 1),
            end_date=date(2027, 1, 10),
            nightly_rate=Decimal("129.00"),
        )
    )
    study = TariffStudy(
        name="Test enero 2027",
        period_start=date(2027, 1, 6),
        period_end=date(2027, 1, 6),
        guests=2,
        nights=7,
        tariff_names=["Grupo SA"],
        parameters={"target_occupancy": 0.5},
        status="pending",
        algorithm_version="1.0",
    )
    db.add(study)
    db.commit()
    return study


def test_calculation_uses_holiday_history_market_median_and_price_guardrail(db):
    study = _make_study_fixture(db)
    register_market_provider(StubMarketProvider())
    try:
        result = asyncio.run(calculate_study(db, study))
    finally:
        register_market_provider(None)

    assert result["status"] == "completed"
    assert result["generated_days"] == 1
    assert result["daily"][0]["holiday_names"] == ["Epifanía"]
    assert result["daily"][0]["historical_adr"] == "155.00"
    assert result["daily"][0]["market_median"] == "140.00"
    assert result["daily"][0]["recommended_rate"] == "154.00"
    assert result["seasons"][0]["start_date"] == "2027-01-06"
    assert result["seasons"][0]["end_date"] == "2027-01-06"
    assert result["comparables"][0]["url"].startswith("https://")
    assert db.query(TariffSeason).count() == 1

    study.result = result
    study.status = "completed"
    db.commit()
    assert tariffs_csv(study).decode("utf-8-sig").splitlines()[1].endswith(",154.00")
    assert study_report_pdf(study).startswith(b"%PDF-")


def test_incomplete_inventory_keeps_adr_but_suppresses_occupancy_and_revpar(db):
    study = _make_study_fixture(db)
    db.query(AvailabilitySlot).delete()
    db.commit()
    register_market_provider(StubMarketProvider())
    try:
        result = asyncio.run(calculate_study(db, study))
    finally:
        register_market_provider(None)

    daily = result["daily"][0]
    assert result["status"] == "partial"
    assert daily["historical_adr"] == "155.00"
    assert daily["historical_occupancy"] is None
    assert daily["historical_revpar"] is None
    assert daily["recommended_rate"] == "140.00"
    assert daily["demand_adjustment"] is None
    assert daily["reason"] is None
    assert result["history_coverage"]["Grupo SA"]["days_with_inventory"] == 0
    assert result["history_coverage"]["Grupo SA"]["uncovered_sold_nights"] == 2
    assert any(gap["reason"] == "occupancy_not_reliable" for gap in result["gaps"])


def test_study_freezes_mapping_rates_and_source_batch_cutoffs(db):
    _make_study_fixture(db)
    start = date(date.today().year + 1, 1, 6)
    provider = StubMarketProvider()
    register_market_provider(provider)
    study = create_study(
        db,
        {
            "name": "Estudio con instantánea",
            "period_start": start,
            "period_end": start,
            "guests": 2,
            "nights": 7,
            "tariff_names": ["Grupo SA"],
            "target_occupancy": 0.5,
        },
        actor_user_id=1,
    )
    snapshot = study.parameters["input_snapshot"]
    assert snapshot["assignments"][0]["municipality"] == "Alfaz del Pi"
    assert snapshot["tariff_seasons"][0]["nightly_rate"] == "129.00"
    assert snapshot["source_batches"]["availability_through"]

    assignment = db.get(TariffGroupAssignment, "P-1")
    rate = db.query(TariffSeason).filter_by(tariff_name="Grupo SA").one()
    assignment.municipality = "Municipio nuevo"
    rate.nightly_rate = Decimal("250.00")
    latest_reservations = ImportBatch(
        dataset="reservations",
        source="upload",
        filename="reservations-later.csv",
        checksum="later",
        status="valid",
        row_count=1,
    )
    db.add(latest_reservations)
    db.flush()
    db.add(
        ReservationSnapshot(
            batch_id=latest_reservations.id,
            source_batch_id=latest_reservations.id,
            source_row=3,
            row_hash="later-r",
            locator="R-2026",
            property_id="P-1",
            status_original="Confirmada",
            status="confirmed",
            created_on=date(2026, 1, 1),
            start_date=date(2026, 1, 6),
            end_date=date(2026, 1, 7),
            nights=1,
            adults=2,
            children=0,
            infants=0,
            display_name="Huésped",
            portal="Directo",
            total=Decimal("999"),
            net=Decimal("999"),
            tax=Decimal("0"),
            paid=Decimal("999"),
            pending=Decimal("0"),
        )
    )
    db.commit()

    try:
        result = asyncio.run(calculate_study(db, study))
    finally:
        register_market_provider(None)

    assert provider.requests[0].municipality == "Alfaz del Pi"
    assert result["daily"][0]["current_rate"] == "129.00"
    assert result["daily"][0]["historical_adr"] == "155.00"
    assert result["source_batches"] == snapshot["source_batches"]


def test_historical_metrics_exclude_cancellation_and_owner_use_and_count_each_stay_night(db):
    _make_study_fixture(db)
    reservation_batch = db.query(ImportBatch).filter_by(dataset="reservations").one()
    for locator, status, start, end, net in (
        ("CANCELLED", "cancelled", date(2025, 1, 7), date(2025, 1, 8), "80"),
        ("OWNER", "owner", date(2025, 1, 8), date(2025, 1, 9), "0"),
        ("CROSS", "confirmed", date(2025, 1, 9), date(2025, 1, 11), "300"),
    ):
        db.add(
            ReservationSnapshot(
                batch_id=reservation_batch.id,
                source_batch_id=reservation_batch.id,
                source_row=20 + len(locator),
                row_hash=f"{locator}-row",
                locator=locator,
                property_id="P-1",
                status_original=status,
                status=status,
                created_on=date(2024, 12, 1),
                start_date=start,
                end_date=end,
                nights=(end - start).days,
                adults=2,
                children=0,
                infants=0,
                display_name="Huésped",
                portal="Directo",
                total=Decimal(net),
                net=Decimal(net),
                tax=Decimal("0"),
                paid=Decimal(net),
                pending=Decimal("0"),
            )
        )
    db.commit()
    assignments = {"P-1": db.get(TariffGroupAssignment, "P-1")}
    metrics, _ = _historical_day_metrics(db, assignments, date(2027, 1, 6), date(2027, 1, 6))

    assert metrics["Grupo SA"][date(2025, 1, 7)]["sold"] == 0
    assert metrics["Grupo SA"][date(2025, 1, 8)]["available"] == 0
    assert metrics["Grupo SA"][date(2025, 1, 9)]["sold"] == 1
    assert metrics["Grupo SA"][date(2025, 1, 10)]["sold"] == 1
    assert metrics["Grupo SA"][date(2025, 1, 9)]["adr"] == Decimal("150")


def test_property_mapping_api_is_admin_only_and_preserves_existing_tariffs(db, monkeypatch):
    register_market_provider(None)
    settings = get_settings()
    monkeypatch.setattr(settings, "app_secret", "unit-test-secret-with-more-than-32-characters")
    property_batch = ImportBatch(
        dataset="properties",
        source="upload",
        filename="properties.csv",
        checksum="p",
        status="valid",
        row_count=1,
    )
    db.add(property_batch)
    db.flush()
    db.add(
        PropertySnapshot(
            batch_id=property_batch.id,
            source_row=3,
            row_hash="p1",
            property_id="P-1",
            name="Alojamiento 1",
            status_original="Activado",
            active=True,
            category="Apartamento",
            capacity=4,
            building="Edificio A",
            city="Alfaz del Pi",
            address=None,
        )
    )
    db.add(
        TariffSeason(
            tariff_name="Grupo SA",
            rate_plan="SA",
            start_date=date(2027, 1, 1),
            end_date=date(2027, 1, 10),
            nightly_rate=Decimal("129.00"),
        )
    )
    admin = User(
        username="lab-admin", password_hash=hasher.hash("very-long-password"), role="admin"
    )
    operator = User(
        username="lab-operator", password_hash=hasher.hash("very-long-password"), role="operator"
    )
    db.add_all([admin, operator])
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            admin_token = client.post(
                "/api/login",
                json={
                    "username": "lab-admin",
                    "password": "very-long-password",
                },
            ).json()["token"]
            operator_token = client.post(
                "/api/login",
                json={
                    "username": "lab-operator",
                    "password": "very-long-password",
                },
            ).json()["token"]
            assert client.get("/api/tariff-lab/readiness").status_code == 401
            assert (
                client.put(
                    "/api/tariff-lab/property-groups",
                    json={"items": []},
                    headers={"Authorization": f"Bearer {operator_token}"},
                ).status_code
                == 403
            )
            response = client.put(
                "/api/tariff-lab/property-groups",
                json={
                    "items": [
                        {
                            "property_id": "P-1",
                            "tariff_name": "Grupo SA",
                            "municipality": "Alfaz del Pi",
                            "province": "Alicante",
                            "autonomous_community": "Comunitat Valenciana",
                        }
                    ]
                },
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            provider_required = client.post(
                "/api/tariff-lab/studies",
                json={
                    "name": "Temporada 2027",
                    "period_start": "2027-01-01",
                    "period_end": "2027-01-31",
                    "guests": 2,
                    "nights": 7,
                    "tariff_names": ["Grupo SA"],
                },
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            tariffs_before_after = client.get(
                "/api/tariffs",
                headers={"Authorization": f"Bearer {operator_token}"},
            )
        assert response.status_code == 200
        assert response.json()[0]["tariff_name"] == "Grupo SA"
        assert provider_required.status_code == 503
        assert tariffs_before_after.status_code == 200
        assert tariffs_before_after.json()[0]["seasons"][0]["nightly_rate"] == "129.00"
    finally:
        app.dependency_overrides.clear()
