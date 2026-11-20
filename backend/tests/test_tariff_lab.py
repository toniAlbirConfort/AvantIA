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
from app.business_scope import MUNICIPALITY
from app.config import get_settings
from app.db import get_db
from app.main import app
from app.models import (
    AvailabilitySlot,
    ImportBatch,
    Job,
    PropertySnapshot,
    ReservationSnapshot,
    TariffHoliday,
    TariffSeason,
    TariffStudy,
    User,
)
from app.tariff_lab import (
    MarketObservation,
    MarketSearchRequest,
    PropertyGroup,
    TariffLabError,
    _base_rate,
    _effective_reservations,
    _historical_day_metrics,
    _holiday_matches,
    _merge_seasons,
    calculate_study,
    create_study,
    infer_property_groups,
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


def test_inferred_groups_migration_drops_manual_data_and_adds_sources():
    path = MIGRATION_PATH.with_name("0026_tariff_lab_inferred_groups.py")
    spec = importlib.util.spec_from_file_location("tariff_lab_inferred_groups", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite+pysqlite://")
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                TARIFF_LAB_MIGRATION.upgrade()
                connection.execute(
                    text(
                        "INSERT INTO tariff_holidays"
                        " (holiday_date, name, scope, source, created_at)"
                        " VALUES ('2027-01-06', 'Manual', 'national', 'manual', '2026-01-01'),"
                        " ('2027-01-01', 'Oficial', 'national', 'boe', '2026-01-01')"
                    )
                )
                migration.upgrade()
                # Running it again is harmless: fresh databases already match the models.
                migration.upgrade()
            tables = set(
                connection.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).scalars()
            )
            columns = {
                row[1] for row in connection.execute(text("PRAGMA table_info(tariff_holidays)"))
            }
            names = set(connection.execute(text("SELECT name FROM tariff_holidays")).scalars())
        assert "tariff_group_assignments" not in tables
        assert {"source_reference", "source_url"} <= columns
        assert names == {"Oficial"}
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
    assignment = PropertyGroup(property_id="P-1", tariff_name="Grupo SA")
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
            municipality="L'Alfas del Pi",
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
        TariffHoliday(
            holiday_date=date(2027, 11, 9),
            name="Fiestas locales",
            scope="municipal",
            municipality=MUNICIPALITY,
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
    assert snapshot["assignments"][0]["municipality"] == MUNICIPALITY
    assert snapshot["tariff_seasons"][0]["nightly_rate"] == "129.00"
    assert snapshot["source_batches"]["availability_through"]

    rate = db.query(TariffSeason).filter_by(tariff_name="Grupo SA").one()
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

    assert provider.requests[0].municipality == MUNICIPALITY
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
    assignments = {"P-1": PropertyGroup(property_id="P-1", tariff_name="Grupo SA")}
    metrics, _ = _historical_day_metrics(db, assignments, date(2027, 1, 6), date(2027, 1, 6))

    assert metrics["Grupo SA"][date(2025, 1, 7)]["sold"] == 0
    assert metrics["Grupo SA"][date(2025, 1, 8)]["available"] == 0
    assert metrics["Grupo SA"][date(2025, 1, 9)]["sold"] == 1
    assert metrics["Grupo SA"][date(2025, 1, 10)]["sold"] == 1
    assert metrics["Grupo SA"][date(2025, 1, 9)]["adr"] == Decimal("150")


def test_tariff_lab_api_is_read_only_for_groups_and_holidays(db, monkeypatch):
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
            tokens = {
                name: client.post(
                    "/api/login", json={"username": name, "password": "very-long-password"}
                ).json()["token"]
                for name in ("lab-admin", "lab-operator")
            }
            admin_headers = {"Authorization": f"Bearer {tokens['lab-admin']}"}
            operator_headers = {"Authorization": f"Bearer {tokens['lab-operator']}"}
            assert client.get("/api/tariff-lab/readiness").status_code == 401
            groups = client.get("/api/tariff-lab/property-groups", headers=operator_headers)
            manual_mapping = client.put(
                "/api/tariff-lab/property-groups", json={"items": []}, headers=admin_headers
            )
            manual_holiday = client.post(
                "/api/tariff-lab/holidays",
                json={"holiday_date": "2027-01-06", "name": "Manual", "scope": "national"},
                headers=admin_headers,
            )
            sync_status = client.get("/api/tariff-lab/holidays/sync", headers=operator_headers)
            operator_sync = client.post("/api/tariff-lab/holidays/sync", headers=operator_headers)
            admin_sync = client.post("/api/tariff-lab/holidays/sync", headers=admin_headers)
            readiness = client.get("/api/tariff-lab/readiness", headers=operator_headers)
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
                headers=admin_headers,
            )
            tariffs_after = client.get("/api/tariffs", headers=operator_headers)
        assert groups.status_code == 200
        assert groups.json()[0]["tariff_name"] == "Grupo SA"
        assert groups.json()[0]["method"] == "single_group"
        assert manual_mapping.status_code == 405
        assert manual_holiday.status_code == 405
        assert sync_status.status_code == 200
        assert operator_sync.status_code == 403
        assert admin_sync.status_code == 202
        assert db.query(Job).filter_by(kind="holiday_sync").count() == 1
        assert readiness.json()["scope"]["municipality"] == MUNICIPALITY
        assert provider_required.status_code == 503
        assert tariffs_after.status_code == 200
        assert tariffs_after.json()[0]["seasons"][0]["nightly_rate"] == "129.00"
    finally:
        app.dependency_overrides.clear()


def _add_property(db, batch_id, property_id, name, building, capacity):
    db.add(
        PropertySnapshot(
            batch_id=batch_id,
            source_row=int(property_id[2:]),
            row_hash=property_id,
            property_id=property_id,
            name=name,
            status_original="Activado",
            active=True,
            category="Apartamento",
            capacity=capacity,
            building=building,
            city="Albir",
            address=None,
        )
    )


def _add_stay(db, batch_id, locator, property_id, start, nightly_total):
    db.add(
        ReservationSnapshot(
            batch_id=batch_id,
            source_batch_id=batch_id,
            source_row=3,
            row_hash=locator,
            locator=locator,
            property_id=property_id,
            status_original="Pagada",
            status="paid",
            created_on=start,
            start_date=start,
            end_date=date.fromordinal(start.toordinal() + 2),
            nights=2,
            adults=2,
            children=0,
            infants=0,
            display_name="Huésped",
            portal="Directo",
            total=Decimal(nightly_total) * 2,
            net=Decimal(nightly_total) * 2,
            tax=Decimal("0"),
            paid=Decimal(nightly_total) * 2,
            pending=Decimal("0"),
        )
    )


def test_groups_are_inferred_from_reservation_prices_and_building_names(db):
    property_batch = ImportBatch(
        dataset="properties", source="t", filename="p", checksum="p", status="valid", row_count=4
    )
    reservation_batch = ImportBatch(
        dataset="reservations", source="t", filename="r", checksum="r", status="valid", row_count=9
    )
    db.add_all([property_batch, reservation_batch])
    db.flush()
    _add_property(db, property_batch.id, "P-10", "AVDA. BLQ.A - 1ºA", "Confort - Blq. A", 3)
    _add_property(db, property_batch.id, "P-11", "AVDA. BLQ.A - 2ºA", "Confort - Blq. A", 3)
    _add_property(db, property_batch.id, "P-20", "AVDA. BLQ.B - 1ºA", "Confort - Blq. B", 4)
    _add_property(db, property_batch.id, "P-30", "Estrella 1º", "Confort - Estrella", 5)
    _add_property(db, property_batch.id, "P-40", "Villa nueva", "Otro edificio", 8)
    for name, rate in (
        ("Confort 1 dormitorio", "100"),
        ("AVENIDA 2 dormitorios", "150"),
        ("ESTRELLA 2 dormitorios", "140"),
    ):
        db.add(
            TariffSeason(
                tariff_name=name,
                rate_plan="SA",
                start_date=date(2026, 9, 1),
                end_date=date(2026, 12, 31),
                nightly_rate=Decimal(rate),
            )
        )
    stays = [
        # Block A matches the one-bedroom rate clearly.
        ("P-10", "98"), ("P-11", "102"), ("P-10", "101"),
        # Block B is between the Estrella and Avenida rates: the name decides.
        ("P-20", "141"), ("P-20", "143"), ("P-20", "146"),
        # Estrella is a near tie too and its own name wins.
        ("P-30", "146"), ("P-30", "144"), ("P-30", "147"),
    ]
    for index, (property_id, nightly) in enumerate(stays):
        _add_stay(
            db, reservation_batch.id, f"L-{index}", property_id, date(2026, 10, 1 + index), nightly
        )
    db.commit()

    result = {row["property_id"]: row for row in infer_property_groups(db)}

    assert result["P-10"]["tariff_name"] == "Confort 1 dormitorio"
    assert result["P-11"]["tariff_name"] == "Confort 1 dormitorio"
    assert result["P-10"]["priced_stays"] == 3
    assert result["P-20"]["tariff_name"] == "AVENIDA 2 dormitorios"
    assert result["P-20"]["method"] == "reservations_and_name"
    assert result["P-20"]["confidence"] == "media"
    assert result["P-30"]["tariff_name"] == "ESTRELLA 2 dormitorios"
    assert result["P-40"]["tariff_name"] is None
    readiness = tariff_readiness(db)
    assert [item["id"] for item in readiness["unmapped_properties"]] == ["P-40"]
    assert readiness["checks"][1]["ready"] is False


def test_effective_reservations_keep_only_the_latest_version_of_each_locator(db):
    batches = []
    for name, status in (("r1", "valid"), ("r2", "valid"), ("r3", "invalid"), ("r4", "valid")):
        batch = ImportBatch(
            dataset="reservations", source="t", filename=name, checksum=name, status=status,
            row_count=2,
        )
        db.add(batch)
        db.flush()
        batches.append(batch)
    first, second, rejected, last = batches
    _add_stay(db, first.id, "A", "P-1", date(2026, 1, 5), "100")
    _add_stay(db, first.id, "B", "P-1", date(2026, 2, 5), "100")
    _add_stay(db, second.id, "A", "P-1", date(2026, 1, 5), "120")
    _add_stay(db, second.id, "C", "P-2", date(2026, 3, 5), "90")
    _add_stay(db, rejected.id, "A", "P-1", date(2026, 1, 5), "999")
    _add_stay(db, last.id, "B", "P-1", date(2026, 2, 5), "130")
    db.commit()

    latest = {row.locator: row.total for row in _effective_reservations(db)}
    assert latest == {"A": Decimal("240"), "B": Decimal("260"), "C": Decimal("180")}
    assert [row.locator for row in _effective_reservations(db)] == ["A", "B", "C"]

    frozen = {row.locator: row.total for row in _effective_reservations(db, second.id)}
    assert frozen == {"A": Decimal("240"), "B": Decimal("200"), "C": Decimal("180")}
    assert _effective_reservations(db, 0) == []
