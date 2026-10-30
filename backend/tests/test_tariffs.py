from __future__ import annotations

import importlib.util
from datetime import date, timedelta
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
from app.models import TariffSeason, User

MIGRATION_PATH = Path(__file__).parents[1] / "alembic" / "versions" / "0008_tariff_seasons.py"
MIGRATION_SPEC = importlib.util.spec_from_file_location("tariff_seasons_migration", MIGRATION_PATH)
assert MIGRATION_SPEC and MIGRATION_SPEC.loader
TARIFF_MIGRATION = importlib.util.module_from_spec(MIGRATION_SPEC)
MIGRATION_SPEC.loader.exec_module(TARIFF_MIGRATION)

EXPECTED_RATES = {
    "Albir Confort 1 dormitorio": [
        149, 109, 119, 129, 119, 129, 139, 119, 139, 189, 169, 159, 119, 129
    ],
    "AVENIDA 2 dorm / Golf II -2112-": [
        189, 149, 139, 149, 139, 149, 169, 149, 179, 259, 249, 209, 159, 149
    ],
    "AVENIDA 3 dormitorios": [
        219, 169, 159, 179, 169, 179, 199, 179, 239, 299, 289, 239, 189, 179
    ],
    "ESTRELLA 2 dormitorios": [
        169, 119, 129, 139, 129, 139, 159, 139, 199, 219, 209, 189, 129, 139
    ],
}


def test_tariff_migration_creates_56_non_overlapping_rates():
    engine = create_engine("sqlite+pysqlite://")
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                TARIFF_MIGRATION.upgrade()
            rows = connection.execute(
                text(
                    "SELECT tariff_name, rate_plan, start_date, end_date, nightly_rate "
                    "FROM tariff_seasons ORDER BY tariff_name, start_date"
                )
            ).all()

        assert len(rows) == 56
        assert {row.tariff_name for row in rows} == set(EXPECTED_RATES)
        for name, prices in EXPECTED_RATES.items():
            seasons = [row for row in rows if row.tariff_name == name]
            assert len(seasons) == 14
            assert [Decimal(str(row.nightly_rate)) for row in seasons] == [
                Decimal(price) for price in prices
            ]
            assert all(row.rate_plan == "SA" for row in seasons)
            for previous, current in zip(seasons, seasons[1:], strict=False):
                previous_end = date.fromisoformat(previous.end_date)
                current_start = date.fromisoformat(current.start_date)
                assert previous_end < current_start
                assert previous_end + timedelta(days=1) == current_start
    finally:
        engine.dispose()


def test_tariffs_endpoint_is_authenticated_and_returns_seeded_groups(db, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_secret", "unit-test-secret-with-more-than-32-characters")
    db.add(
        User(
            username="tariff-operator",
            password_hash=hasher.hash("very-long-tariff-password"),
            role="operator",
        )
    )
    db.add_all(TariffSeason(**row) for row in TARIFF_MIGRATION.SEED_ROWS)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            assert client.get("/api/tariffs").status_code == 401
            login = client.post(
                "/api/login",
                json={"username": "tariff-operator", "password": "very-long-tariff-password"},
            )
            response = client.get(
                "/api/tariffs",
                headers={"Authorization": f"Bearer {login.json()['token']}"},
            )

        assert response.status_code == 200
        groups = response.json()
        assert [group["name"] for group in groups] == sorted(EXPECTED_RATES)
        assert all(len(group["seasons"]) == 14 for group in groups)
        albir = next(group for group in groups if group["name"] == "Albir Confort 1 dormitorio")
        assert albir["seasons"][0] == {
            "rate_plan": "SA",
            "start_date": "2026-09-01",
            "end_date": "2026-11-22",
            "nightly_rate": "149.00",
        }
    finally:
        app.dependency_overrides.clear()
