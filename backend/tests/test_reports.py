from __future__ import annotations

import importlib.util
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.auth import hasher
from app.config import get_settings
from app.db import get_db
from app.main import app
from app.models import Report, TariffStudy, User
from app.reports import ReportError, create_report, list_reports, report_document

MIGRATION_PATH = Path(__file__).parents[1] / "alembic" / "versions" / "0013_reports.py"
MIGRATION_SPEC = importlib.util.spec_from_file_location("reports_migration", MIGRATION_PATH)
assert MIGRATION_SPEC and MIGRATION_SPEC.loader
REPORTS_MIGRATION = importlib.util.module_from_spec(MIGRATION_SPEC)
MIGRATION_SPEC.loader.exec_module(REPORTS_MIGRATION)

UNPUBLISH_PATH = MIGRATION_PATH.with_name("0014_unpublished_report_link.py")
UNPUBLISH_SPEC = importlib.util.spec_from_file_location("unpublish_migration", UNPUBLISH_PATH)
assert UNPUBLISH_SPEC and UNPUBLISH_SPEC.loader
UNPUBLISH_MIGRATION = importlib.util.module_from_spec(UNPUBLISH_SPEC)
UNPUBLISH_SPEC.loader.exec_module(UNPUBLISH_MIGRATION)


def study(name: str, status: str, completed_at: datetime | None) -> TariffStudy:
    return TariffStudy(
        name=name, period_start=date(2027, 1, 1), period_end=date(2027, 1, 31),
        guests=2, nights=7, tariff_names=["Grupo SA"], parameters={}, status=status,
        created_at=datetime(2026, 11, 1, tzinfo=UTC), completed_at=completed_at,
    )


def test_migration_creates_reports_and_seeds_todays_competitor_study():
    engine = create_engine("sqlite+pysqlite://")
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                REPORTS_MIGRATION.upgrade()
            rows = connection.execute(
                text("SELECT title, source, html_body FROM reports")
            ).all()
        assert len(rows) == 1
        title, source, html_body = rows[0]
        assert title == "Precios de la competencia profesional en Albir"
        assert source == "claude"
        assert "Nuestra tarifa frente a la mediana" in html_body
    finally:
        engine.dispose()


def test_migration_drops_link_to_unpublished_claude_copy():
    engine = create_engine("sqlite+pysqlite://")
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                REPORTS_MIGRATION.upgrade()
                connection.execute(
                    text("UPDATE reports SET original_url = :url"),
                    {"url": UNPUBLISH_MIGRATION.UNPUBLISHED_URL},
                )
                UNPUBLISH_MIGRATION.upgrade()
            urls = connection.execute(text("SELECT original_url FROM reports")).scalars().all()
        assert urls == [None]
    finally:
        engine.dispose()


def test_list_merges_stored_reports_and_finished_studies_newest_first(db):
    db.add(Report(
        title="Competencia Albir", source="claude", html_body="<p>hola</p>",
        created_at=datetime(2026, 11, 13, 12, tzinfo=UTC),
    ))
    db.add(study("Invierno", "completed", datetime(2026, 11, 10, tzinfo=UTC)))
    db.add(study("Verano", "partial", datetime(2026, 11, 14, tzinfo=UTC)))
    db.add(study("Fallido", "failed", None))
    db.commit()

    items = list_reports(db)

    assert [item["title"] for item in items] == ["Verano", "Competencia Albir", "Invierno"]
    assert [item["kind"] for item in items] == ["tariff_study", "document", "tariff_study"]
    assert items[0]["source"] == "app" and items[1]["source"] == "claude"


def test_document_is_wrapped_and_links_open_outside_the_viewer():
    fragment = report_document(Report(title="x", source="claude", html_body="<p>a</p>"))
    assert fragment.startswith("<!doctype html>")
    assert '<base target="_blank">' in fragment
    full = report_document(Report(
        title="x", source="claude", html_body="<html><head><title>t</title></head><body></body></html>",
    ))
    assert full.startswith('<html><head><base target="_blank">')


@pytest.mark.parametrize(
    ("title", "raw", "source", "url", "message"),
    [
        ("  ", b"<p>a</p>", "claude", None, "título"),
        ("Informe", b"", "claude", None, "vacío"),
        ("Informe", b"\xff\xfe\x00", "claude", None, "UTF-8"),
        ("Informe", b"<p>a</p>", "otro", None, "Origen"),
        ("Informe", b"<p>a</p>", "claude", "http://example.test", "https://"),
    ],
)
def test_create_report_validates_input(db, title, raw, source, url, message):
    with pytest.raises(ReportError, match=message):
        create_report(
            db, title=title, description=None, raw=raw, source=source,
            original_url=url, user_id=None,
        )


def test_reports_api_lists_for_operators_and_uploads_for_admins(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "app_secret", "unit-test-secret-with-more-than-32-characters")
    db.add_all([
        User(username="rep-admin", password_hash=hasher.hash("very-long-password"), role="admin"),
        User(username="rep-operator", password_hash=hasher.hash("very-long-password"), role="operator"),
    ])
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            def login(username: str) -> dict:
                token = client.post(
                    "/api/login", json={"username": username, "password": "very-long-password"},
                ).json()["token"]
                return {"Authorization": f"Bearer {token}"}

            admin, operator = login("rep-admin"), login("rep-operator")
            files = {"file": ("informe.html", b"<h1>Ocupaci\xc3\xb3n</h1>", "text/html")}
            assert client.get("/api/reports").status_code == 401
            assert client.post(
                "/api/reports", data={"title": "Ocupación"}, files=files, headers=operator,
            ).status_code == 403
            created = client.post(
                "/api/reports", data={"title": "Ocupación", "description": "Noviembre"},
                files=files, headers=admin,
            )
            listed = client.get("/api/reports", headers=operator)
            content = client.get(f"/api/reports/{created.json()['id']}/content", headers=operator)
            missing = client.get("/api/reports/999/content", headers=operator)
            deleted = client.delete(f"/api/reports/{created.json()['id']}", headers=admin)
        assert created.status_code == 201
        assert listed.json()[0]["title"] == "Ocupación"
        assert listed.json()[0]["created_by"] == "rep-admin"
        assert content.headers["content-type"].startswith("text/html")
        assert "sandbox" in content.headers["content-security-policy"]
        assert "<h1>Ocupación</h1>" in content.text
        assert missing.status_code == 404
        assert deleted.json() == {"deleted": True}
    finally:
        app.dependency_overrides.clear()
