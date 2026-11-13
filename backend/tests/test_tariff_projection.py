from __future__ import annotations

import importlib.util
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.auth import hasher
from app.config import get_settings
from app.db import Base, get_db
from app.main import app
from app.models import (
    ImportBatch,
    PropertySnapshot,
    ReservationSnapshot,
    TariffGroupAssignment,
    TariffMarketObservation,
    TariffSeason,
    User,
)
from app.tariff_lab import TariffLabError
from app.tariff_projection import (
    _round_rate,
    apply_projection,
    calculate_projection,
    create_projection,
    easter_sunday,
    final_seasons,
    holy_week,
    normalize_parameters,
    shift_date,
    update_projection,
)

MIGRATION_PATH = Path(__file__).parents[1] / "alembic" / "versions" / "0019_tariff_projections.py"
SPEC = importlib.util.spec_from_file_location("tariff_projection_migration", MIGRATION_PATH)
assert SPEC and SPEC.loader
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)

GROUP = "Grupo playa"
SMALL = "Grupo pequeño"
# 2027 SA seasons; Holy Week 2027 falls inside 19-31 March (Easter Sunday 28 March).
SEASONS_2027 = [
    (date(2027, 1, 1), date(2027, 2, 28), "100.00"),
    (date(2027, 3, 1), date(2027, 3, 18), "110.00"),
    (date(2027, 3, 19), date(2027, 3, 31), "140.00"),
    (date(2027, 4, 1), date(2027, 6, 30), "120.00"),
    (date(2027, 7, 1), date(2027, 8, 31), "200.00"),
    (date(2027, 9, 1), date(2027, 12, 31), "130.00"),
]


def _batch(db, dataset: str, created_at: datetime) -> ImportBatch:
    batch = ImportBatch(
        dataset=dataset, source="upload", filename=f"{dataset}.csv", checksum=dataset,
        status="valid", row_count=1, created_at=created_at,
    )
    db.add(batch)
    db.flush()
    return batch


def _reservation(db, batch, locator, property_id, start, nights, total, created_on=None):
    db.add(ReservationSnapshot(
        batch_id=batch.id, source_row=1, row_hash=locator, locator=locator,
        property_id=property_id, status_original="Pagada", status="paid",
        created_on=created_on or start - timedelta(days=60), start_date=start,
        end_date=start + timedelta(days=nights), nights=nights, adults=2, children=0,
        infants=0, display_name="Cliente", portal=None, total=Decimal(total),
        net=Decimal(total), tax=Decimal(0), paid=Decimal(total), pending=Decimal(0),
    ))


def _seed(db) -> None:
    """Three units in GROUP, one in SMALL; history for 2025 and 2026."""
    as_of = datetime(2026, 10, 27, 12, tzinfo=UTC)
    properties = _batch(db, "properties", as_of)
    for index, (property_id, group) in enumerate(
        [("A1", GROUP), ("A2", GROUP), ("A3", GROUP), ("S1", SMALL)]
    ):
        db.add(PropertySnapshot(
            batch_id=properties.id, source_row=index + 2, row_hash=property_id,
            property_id=property_id, name=property_id, status_original="Activado",
            active=True, category="Apartamento", capacity=4, building=None,
            city="L'Alfàs del Pi", address=None,
        ))
        db.add(TariffGroupAssignment(
            property_id=property_id, tariff_name=group, municipality="L'Alfàs del Pi"
        ))
    for group in (GROUP, SMALL):
        for start, end, rate in SEASONS_2027:
            db.add(TariffSeason(
                tariff_name=group, rate_plan="SA", start_date=start, end_date=end,
                nightly_rate=Decimal(rate),
            ))
    reservations = _batch(db, "reservations", as_of)
    # Summer 2026 fully booked in all three units (62 nights each): occupancy 100 %.
    for unit in ("A1", "A2", "A3"):
        _reservation(db, reservations, f"S26-{unit}", unit, date(2026, 7, 1), 62, "11160.00")
    # April-June 2026: one unit for 30 nights out of 3 x 91: 11 % occupancy;
    # April-June 2025: two units for 90 nights: 66 %. Low and falling.
    _reservation(db, reservations, "Q26", "A1", date(2026, 4, 1), 20, "2400.00")
    _reservation(db, reservations, "Q26b", "A1", date(2026, 5, 1), 10, "1200.00")
    for unit in ("A1", "A2"):
        for month in (4, 5, 6):
            _reservation(
                db, reservations, f"Q25-{unit}-{month}", unit, date(2025, month, 1), 30,
                "3000.00",
            )
    # Booked for 2027 by the cut-off date versus the same point of 2025 for 2026.
    _reservation(
        db, reservations, "P27", "A2", date(2027, 2, 1), 7, "700.00",
        created_on=date(2026, 9, 1),
    )
    _reservation(
        db, reservations, "P26", "A3", date(2026, 2, 1), 14, "1400.00",
        created_on=date(2025, 9, 1),
    )
    # Market: summer competitors 30 % above our 200 EUR; three references.
    for competitor, price in (("Hotel 1", 250), ("Hotel 2", 260), ("Hotel 3", 270)):
        db.add(TariffMarketObservation(
            tariff_name=GROUP, competitor=competitor, unit_label="1 dorm.",
            stay_date=date(2027, 8, 9), nights=7, guests=2, nightly_rate=Decimal(price),
            source="Booking", observed_on=date(2026, 11, 13),
        ))
    # Autumn 2026 prices for the 1 Sep - 31 Dec season, inflated one year: 100 -> 103.7.
    for competitor in ("Hotel 1", "Hotel 2"):
        db.add(TariffMarketObservation(
            tariff_name=GROUP, competitor=competitor, unit_label="1 dorm.",
            stay_date=date(2026, 11, 27), nights=3, guests=2, nightly_rate=Decimal(100),
            source="Booking", observed_on=date(2026, 11, 13),
        ))
    db.commit()


def _season(result: dict, group: str, start: str) -> dict:
    return next(
        item for item in result["seasons"]
        if item["tariff_name"] == group and item["base_start"] == start
    )


def test_calendar_helpers_follow_easter_and_leap_years():
    assert easter_sunday(2026) == date(2026, 4, 5)
    assert easter_sunday(2027) == date(2027, 3, 28)
    assert easter_sunday(2028) == date(2028, 4, 16)
    assert holy_week(2028) == (date(2028, 4, 7), date(2028, 4, 17))
    assert shift_date(date(2027, 2, 28), 2028, is_end=True) == date(2028, 2, 29)
    assert shift_date(date(2028, 2, 29), 2029, is_end=True) == date(2029, 2, 28)
    assert shift_date(date(2027, 3, 19), 2028) == date(2028, 3, 19)


def test_rounding_modes_and_parameter_limits():
    assert _round_rate(Decimal("164.60"), "euro") == Decimal("165.00")
    assert _round_rate(Decimal("164.60"), "nine") == Decimal("169.00")
    assert _round_rate(Decimal("162.00"), "nine") == Decimal("159.00")
    assert normalize_parameters({"cost_percent": 2})["cost_percent"] == 2.0
    for invalid in ({"cost_percent": 80}, {"unknown": 1}, {"rounding": "cents"},
                    {"min_change_percent": 5, "max_change_percent": 1}):
        try:
            normalize_parameters(invalid)
        except TariffLabError:
            continue
        raise AssertionError(f"accepted {invalid}")


def test_projection_explains_cost_demand_and_market_per_season(db):
    _seed(db)
    result = calculate_projection(
        db, base_year=2027, target_year=2028, groups=[GROUP, SMALL], parameters={}
    )
    assert result["as_of"] == "2026-10-27"

    summer = _season(result, GROUP, "2027-07-01")
    assert summer["start"] == "2028-07-01" and summer["end"] == "2028-08-31"
    assert summer["occupancy_year"] == 2026 and summer["occupancy"] == 1.0
    assert summer["demand_adjustment"] == 0.02
    assert summer["market_median"] == "260.00" and summer["market_refs"] == 3
    # Gap +30 %: a third of it, capped at 5 points.
    assert summer["market_adjustment"] == 0.05
    assert summer["total_change"] == 0.1  # 3.5 + 2 + 5 = 10.5, capped at 10
    assert "limitado" in summer["flags"]
    assert summer["rate"] == "220.00"

    spring = _season(result, GROUP, "2027-04-01")
    assert spring["occupancy"] < 0.6 and spring["previous_occupancy"] > 0.6
    assert spring["demand_adjustment"] == -0.02
    assert spring["rate"] == "122.00"  # 120 x 1.015

    autumn = _season(result, GROUP, "2027-09-01")
    # 1 Sep - 31 Dec has not finished in 2026 yet, so the reference is 2025.
    assert autumn["occupancy_year"] == 2025
    assert autumn["market_median"] == "103.70"  # 2026 prices carried to 2027
    assert autumn["market_adjustment"] == -0.02  # market 20 % below our 130

    winter = _season(result, GROUP, "2027-01-01")
    assert winter["end"] == "2028-02-29"  # leap year keeps February whole

    small = _season(result, SMALL, "2027-07-01")
    assert small["demand_adjustment"] == 0  # one unit is not representative
    assert any(SMALL in warning and "representativa" in warning for warning in result["warnings"])

    pickup = result["groups"][GROUP]["pickup"]
    assert pickup == {"year": 2027, "nights": 7, "previous_nights": 14, "change": -0.5}
    assert result["groups"][GROUP]["units"] == 3


def test_easter_moves_from_march_to_april(db):
    _seed(db)
    result = calculate_projection(
        db, base_year=2027, target_year=2028, groups=[GROUP], parameters={}
    )
    calendar = result["easter"]
    assert calendar["window_start"] == "2028-04-07" and calendar["window_end"] == "2028-04-17"
    pieces = calendar["groups"][GROUP]["seasons"]
    by_start = {item["start"]: item for item in pieces}
    easter_rate = _season(result, GROUP, "2027-03-19")["rate"]
    march_rate = _season(result, GROUP, "2027-03-01")["rate"]
    april_rate = _season(result, GROUP, "2027-04-01")["rate"]
    assert by_start["2028-03-19"]["rate"] == march_rate  # no Holy Week in March 2028
    assert by_start["2028-04-01"]["end"] == "2028-04-06"
    assert by_start["2028-04-01"]["rate"] == april_rate
    assert by_start["2028-04-07"]["end"] == "2028-04-17"
    assert by_start["2028-04-07"]["rate"] == easter_rate
    assert by_start["2028-04-18"]["end"] == "2028-06-30"
    days = sum(
        (date.fromisoformat(item["end"]) - date.fromisoformat(item["start"])).days + 1
        for item in pieces
    )
    assert days == 366


def test_overrides_and_apply_write_target_year_once(db):
    _seed(db)
    projection = create_projection(
        db,
        {"name": "SA 2028", "base_year": 2027, "target_year": 2028,
         "tariff_names": [GROUP], "parameters": {"rounding": "nine"}},
        actor_user_id=None,
    )
    assert projection.move_easter is True
    summer_key = f"{GROUP}|2028-07-01"
    update_projection(projection, {"overrides": {summer_key: {"rate": 215, "reason": "Prudencia"}}})
    rows = {item["key"]: item for item in final_seasons(projection)}
    assert rows[summer_key]["rate"] == "215.00"
    assert rows[summer_key]["override_reason"] == "Prudencia"
    try:
        update_projection(projection, {"overrides": {"otro|2028-01-01": {"rate": 1}}})
    except TariffLabError:
        pass
    else:
        raise AssertionError("override for an unknown season was accepted")

    written = apply_projection(db, projection, actor_user_id=None)
    db.commit()
    seasons_2028 = db.query(TariffSeason).filter(
        TariffSeason.tariff_name == GROUP, TariffSeason.start_date >= date(2028, 1, 1)
    ).order_by(TariffSeason.start_date).all()
    assert written == len(seasons_2028) == 8  # 6 seasons, April split around Holy Week
    assert seasons_2028[0].end_date == date(2028, 2, 29)
    assert projection.status == "applied"
    for action in (
        lambda: apply_projection(db, projection, actor_user_id=None),
        lambda: update_projection(projection, {"notes": "tarde"}),
    ):
        try:
            action()
        except TariffLabError:
            continue
        raise AssertionError("an applied projection changed")


def test_apply_refuses_when_target_year_already_has_tariffs(db):
    _seed(db)
    projection = create_projection(
        db,
        {"name": "SA 2028", "base_year": 2027, "target_year": 2028,
         "tariff_names": [GROUP], "parameters": {}},
        actor_user_id=None,
    )
    db.add(TariffSeason(
        tariff_name=GROUP, rate_plan="SA", start_date=date(2028, 1, 1),
        end_date=date(2028, 1, 31), nightly_rate=Decimal("99.00"),
    ))
    db.flush()
    try:
        apply_projection(db, projection, actor_user_id=None)
    except TariffLabError as exc:
        assert "No se sobrescriben" in str(exc)
    else:
        raise AssertionError("existing 2028 tariffs were overwritten")


def test_projection_api_is_admin_only_and_audited(db, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_secret", "unit-test-secret-with-more-than-32-characters")
    _seed(db)
    db.add_all([
        User(username="proj-admin", password_hash=hasher.hash("very-long-password"), role="admin"),
        User(
            username="proj-operator", password_hash=hasher.hash("very-long-password"),
            role="operator",
        ),
    ])
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            def token(username):
                return client.post(
                    "/api/login", json={"username": username, "password": "very-long-password"}
                ).json()["token"]

            admin = {"Authorization": f"Bearer {token('proj-admin')}"}
            operator = {"Authorization": f"Bearer {token('proj-operator')}"}
            body = {"name": "SA 2028", "base_year": 2027, "target_year": 2028,
                    "tariff_names": [GROUP]}
            assert client.post(
                "/api/tariff-lab/projections", json=body, headers=operator
            ).status_code == 403
            created = client.post("/api/tariff-lab/projections", json=body, headers=admin)
            assert created.status_code == 201, created.text
            projection_id = created.json()["id"]
            assert created.json()["final_seasons"]

            observation = client.post(
                "/api/tariff-lab/market-observations",
                json={"tariff_names": [GROUP, SMALL], "competitor": "Hotel 4",
                      "stay_date": "2027-05-14", "nights": 3, "guests": 2,
                      "nightly_rate": 150, "observed_on": "2026-11-13"},
                headers=admin,
            )
            assert observation.status_code == 201 and len(observation.json()) == 2
            listed = client.get("/api/tariff-lab/market-observations", headers=operator)
            assert any(item["competitor"] == "Hotel 4" for item in listed.json())

            patched = client.patch(
                f"/api/tariff-lab/projections/{projection_id}",
                json={"move_easter": False, "overrides": {f"{GROUP}|2028-07-01": {"rate": 230}}},
                headers=admin,
            )
            assert patched.status_code == 200
            assert len(patched.json()["final_seasons"]) == 6
            csv = client.get(
                f"/api/tariff-lab/projections/{projection_id}/tariffs.csv", headers=operator
            )
            assert "Grupo playa,SA,2028-07-01,2028-08-31,230.00" in csv.text

            applied = client.post(
                f"/api/tariff-lab/projections/{projection_id}/apply", headers=admin
            )
            assert applied.status_code == 200 and applied.json()["status"] == "applied"
            again = client.post(
                f"/api/tariff-lab/projections/{projection_id}/apply", headers=admin
            )
            assert again.status_code == 409
            assert client.delete(
                f"/api/tariff-lab/projections/{projection_id}", headers=admin
            ).status_code == 409
            tariffs = client.get("/api/tariffs", headers=operator).json()
            group = next(item for item in tariffs if item["name"] == GROUP)
            assert {"rate_plan": "SA", "start_date": "2028-07-01", "end_date": "2028-08-31",
                    "nightly_rate": "230.00"} in group["seasons"]
        actions = {
            row[0] for row in db.execute(text("SELECT action FROM audit_events")).all()
        }
        assert {
            "tariff_lab.projection_created", "tariff_lab.projection_updated",
            "tariff_lab.projection_applied", "tariff_lab.market_observation_added",
        } <= actions
    finally:
        app.dependency_overrides.clear()


def test_migration_seeds_the_albir_competitor_prices():
    engine = create_engine("sqlite+pysqlite://")
    try:
        with engine.begin() as connection:
            Base.metadata.tables["users"].create(connection)
            with Operations.context(MigrationContext.configure(connection)):
                MIGRATION.upgrade()
            rows = connection.execute(text(
                "SELECT tariff_name, stay_date, nightly_rate FROM tariff_market_observations"
            )).all()
        assert len(rows) == 37
        august_one_bedroom = sorted(
            float(row[2]) for row in rows
            if row[0] == "Albir Confort 1 dormitorio" and str(row[1]).startswith("2027-08")
        )
        assert august_one_bedroom[len(august_one_bedroom) // 2] == 198
    finally:
        engine.dispose()
