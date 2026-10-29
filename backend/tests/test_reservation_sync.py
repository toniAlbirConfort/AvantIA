from __future__ import annotations

import asyncio
import csv
import io
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.config import Settings
from app.main import integrations
from app.models import ImportBatch, ReservationSnapshot, ReservationSyncState, User
from app.planning import latest_source_batch
from app.reservation_sync import (
    SYNC_STATE_KEY,
    ReservationSyncError,
    download_reservations_csv,
    storage_state_updated_since,
    sync_is_due,
    sync_reservations_if_due,
)

HEADERS = [
    "Localizador", "Fecha alta", "Estado", "Fecha entrada", "Fecha salida", "noches",
    "Total reserva con tasas", "Total reserva sin tasas", "Total tasas", "Pagado", "Pendiente",
    "Adultos", "Niños", "Bebés", "id alojamiento", "Cliente: Nombre", "Cliente: Apellidos",
    "Ocupante: Nombre", "Ocupante: Apellidos", "Portal",
]


def reservation_csv(locator: str = "A-1") -> bytes:
    data = io.StringIO(newline="")
    writer = csv.writer(data)
    writer.writerow(["Listado de reservas"])
    writer.writerow(HEADERS)
    writer.writerow(
        [
            locator, "01/10/2026", "Confirmada", "10/11/2026", "12/11/2026", 2,
            "110.00", "100.00", "10.00", "0.00", "110.00", 2, 0, 0, "PROP-1",
            "Cliente", "Prueba", "Ocupante", "Ejemplo", "WEB",
        ]
    )
    return data.getvalue().encode()


def configured_settings() -> Settings:
    return Settings(
        avantio_export_url="https://app.avantio.pro/index.php?module=Compromisos",
        avantio_username="operador",
        avantio_password="test-password",
        avantio_sync_interval_hours=6,
    )


def test_sync_due_uses_the_configured_interval():
    last_attempt = datetime(2026, 10, 28, 8, tzinfo=UTC)
    assert sync_is_due(None, 6, last_attempt)
    assert not sync_is_due(last_attempt, 6, last_attempt + timedelta(hours=5, minutes=59))
    assert sync_is_due(last_attempt, 6, last_attempt + timedelta(hours=6))


class FakeLocator:
    def __init__(self, page, name: str, visible: bool = True):
        self.page = page
        self.name = name
        self.visible = visible

    async def count(self):
        return 1 if self.visible else 0

    def nth(self, index: int):
        assert index == 0
        return self

    async def is_visible(self):
        return self.visible

    async def wait_for(self, state: str, timeout: int):
        assert state == "hidden"
        assert timeout == 20_000
        if self.name == "password" and self.page.logged_in:
            return
        raise AssertionError("El campo de contraseña debería desaparecer al iniciar sesión")

    async def fill(self, value: str):
        self.page.fills.append((self.name, value))

    async def click(self):
        self.page.actions.append(self.name)
        if self.name == "submit":
            self.page.logged_in = True
        elif self.name == "navigation menu":
            self.page.menu_open = True
        elif self.name == "Reservas":
            self.page.reservas_menu_open = True
        elif self.name == "Listado de reservas":
            self.page.reservation_list_open = True
        elif self.name == "Más acciones":
            self.page.actions_menu_open = True
        elif self.name == "Descargar CSV":
            self.page.download_future.set_result(self.page.download)


class FakeDownload:
    suggested_filename = "reservas.csv"

    def __init__(self, path: Path):
        self._path = path

    async def failure(self):
        return None

    async def path(self):
        return str(self._path)


class FakeDownloadExpectation:
    def __init__(self, page):
        self.page = page
        self.value = page.download_future

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakePage:
    def __init__(self, download: FakeDownload):
        self.download = download
        self.download_future = None
        self.logged_in = False
        self.menu_open = False
        self.reservas_menu_open = False
        self.reservation_list_open = False
        self.actions_menu_open = False
        self.actions = []
        self.fills = []
        self.visited = []
        self.response_status = None

    def set_default_timeout(self, timeout):
        self.timeout = timeout

    async def goto(self, url, **kwargs):
        self.visited.append(url)
        return SimpleNamespace(status=self.response_status) if self.response_status else None

    def locator(self, selector: str):
        if selector == 'button[aria-label*="menu" i]':
            return FakeLocator(self, "navigation menu", visible=self.logged_in)
        if selector == 'input[type="password"]':
            return FakeLocator(self, "password", visible=not self.logged_in)
        if selector == 'input[autocomplete="username"]' or selector == 'input[type="email"]':
            return FakeLocator(self, "username", visible=False)
        if selector == 'input[name*="user" i]':
            return FakeLocator(self, "username", visible=not self.logged_in)
        if selector == 'button[type="submit"]':
            return FakeLocator(self, "submit", visible=not self.logged_in)
        return FakeLocator(self, selector, visible=False)

    def get_by_role(self, role, name, exact):
        if role == "menuitem" and name == "Reservas":
            return FakeLocator(self, name, visible=self.menu_open)
        if role == "menuitem" and name == "Listado de reservas":
            return FakeLocator(self, name, visible=self.reservas_menu_open)
        if role == "button" and name == "Más acciones":
            return FakeLocator(self, name, visible=self.reservation_list_open)
        if role == "menuitem" and name == "Descargar CSV":
            return FakeLocator(self, name, visible=self.actions_menu_open)
        return FakeLocator(self, name, visible=False)

    def get_by_text(self, label, exact):
        return FakeLocator(self, label, visible=False)

    def expect_download(self, timeout):
        self.download_future = asyncio.get_running_loop().create_future()
        return FakeDownloadExpectation(self)


class FakeContext:
    def __init__(self, page):
        self.page = page

    async def new_page(self):
        return self.page


class FakeBrowser:
    def __init__(self, context):
        self.context = context

        self.context_options = None

    async def new_context(self, **kwargs):
        assert kwargs["accept_downloads"] is True
        self.context_options = kwargs
        return self.context

    async def close(self):
        pass


class FakePlaywrightManager:
    def __init__(self, browser):
        self.chromium = SimpleNamespace(launch=self.launch)
        self.browser = browser

    async def launch(self, headless):
        assert headless is False
        return self.browser

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


def test_playwright_login_clicks_export_and_reads_temporary_download(monkeypatch):
    raw = reservation_csv()
    with tempfile.TemporaryDirectory(dir=Path(__file__).parents[1] / ".tmp") as directory:
        path = Path(directory) / "download.csv"
        path.write_bytes(raw)
        page = FakePage(FakeDownload(path))
        browser = FakeBrowser(FakeContext(page))
        monkeypatch.setattr(
            "app.reservation_sync.async_playwright",
            lambda: FakePlaywrightManager(browser),
        )

        filename, downloaded = asyncio.run(download_reservations_csv(configured_settings()))

    assert filename == "reservas.csv"
    assert downloaded == raw
    assert page.actions == [
        "submit",
        "navigation menu",
        "Reservas",
        "Listado de reservas",
        "Más acciones",
        "Descargar CSV",
    ]
    assert page.fills == [("username", "operador"), ("password", "test-password")]
    assert len(page.visited) == 2
    assert page.visited == ["https://app.avantio.pro/"] * 2
    assert browser.context_options == {"accept_downloads": True}


def test_playwright_loads_saved_storage_state_without_printing_it(tmp_path, monkeypatch):
    raw = reservation_csv()
    download_path = tmp_path / "download.csv"
    download_path.write_bytes(raw)
    storage_state_path = tmp_path / "storage-state.json"
    storage_state_path.write_text('{"cookies":[],"origins":[]}', encoding="utf-8")
    page = FakePage(FakeDownload(download_path))
    browser = FakeBrowser(FakeContext(page))
    settings = configured_settings()
    settings.avantio_storage_state_path = str(storage_state_path)
    monkeypatch.setattr(
        "app.reservation_sync.async_playwright",
        lambda: FakePlaywrightManager(browser),
    )

    filename, downloaded = asyncio.run(download_reservations_csv(settings))

    assert filename == "reservas.csv"
    assert downloaded == raw
    assert browser.context_options == {
        "accept_downloads": True,
        "storage_state": str(storage_state_path),
    }


def test_playwright_reports_cloudfront_403_before_login(tmp_path, monkeypatch):
    page = FakePage(FakeDownload(tmp_path / "unused.csv"))
    page.response_status = 403
    browser = FakeBrowser(FakeContext(page))
    monkeypatch.setattr(
        "app.reservation_sync.async_playwright",
        lambda: FakePlaywrightManager(browser),
    )

    with pytest.raises(ReservationSyncError, match="HTTP 403"):
        asyncio.run(download_reservations_csv(configured_settings()))


def test_new_storage_state_triggers_retry_before_interval(tmp_path):
    last_attempt = datetime(2026, 10, 28, 8, tzinfo=UTC)
    state_file = tmp_path / "storage-state.json"
    state_file.write_text("{}", encoding="utf-8")
    updated = last_attempt.timestamp() + 1
    os.utime(state_file, (updated, updated))

    assert storage_state_updated_since(last_attempt, state_file)
    assert not storage_state_updated_since(last_attempt, tmp_path / "missing.json")


def test_repeated_csv_updates_last_success_without_duplicate_batches(
    session_factory, monkeypatch
):
    raw = reservation_csv()
    calls = []

    async def download(settings):
        calls.append(settings.avantio_export_url)
        return "reservas.csv", raw

    monkeypatch.setattr("app.reservation_sync.download_reservations_csv", download)
    settings = configured_settings()
    first = datetime(2026, 10, 28, 8, tzinfo=UTC)
    second = first + timedelta(hours=6)

    assert asyncio.run(sync_reservations_if_due(settings, session_factory, first))
    assert not asyncio.run(
        sync_reservations_if_due(settings, session_factory, first + timedelta(hours=5))
    )
    assert asyncio.run(sync_reservations_if_due(settings, session_factory, second))

    with session_factory() as db:
        state = db.get(ReservationSyncState, SYNC_STATE_KEY)
        assert state is not None
        assert state.status == "succeeded"
        assert state.last_attempt_at.replace(tzinfo=UTC) == second
        assert state.last_success_at.replace(tzinfo=UTC) == second
        assert state.row_count == 1
        assert db.scalar(select(ImportBatch.id).where(ImportBatch.dataset == "reservations"))
        assert db.query(ImportBatch).filter_by(dataset="reservations").count() == 1
    assert len(calls) == 2


def test_invalid_csv_records_failure_and_keeps_last_valid_snapshot(session_factory, monkeypatch):
    valid_raw = reservation_csv()
    current = {"raw": valid_raw}

    async def download(settings):
        return "reservas.csv", current["raw"]

    monkeypatch.setattr("app.reservation_sync.download_reservations_csv", download)
    settings = configured_settings()
    first = datetime(2026, 10, 28, 8, tzinfo=UTC)
    assert asyncio.run(sync_reservations_if_due(settings, session_factory, first))
    current["raw"] = b"no es una exportacion de reservas"
    assert asyncio.run(
        sync_reservations_if_due(settings, session_factory, first + timedelta(hours=6))
    )

    with session_factory() as db:
        state = db.get(ReservationSyncState, SYNC_STATE_KEY)
        assert state is not None and state.status == "failed"
        assert state.last_error
        assert latest_source_batch(db, "reservations").status == "valid"
        assert db.query(ReservationSnapshot).filter_by(locator="A-1").count() == 1
        assert (
            db.query(ImportBatch)
            .filter_by(dataset="reservations", status="invalid")
            .count()
            == 1
        )


def test_integrations_reports_automatic_reservation_sync(db, monkeypatch):
    settings = configured_settings()
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    db.add(
        ReservationSyncState(
            key=SYNC_STATE_KEY,
            status="succeeded",
            last_attempt_at=datetime(2026, 10, 28, 8, tzinfo=UTC),
            last_success_at=datetime(2026, 10, 28, 8, tzinfo=UTC),
            row_count=14,
        )
    )
    db.commit()

    result = integrations(db, User(username="admin", password_hash="unused"))

    assert result["avantio"]["configured"] is True
    assert result["avantio"]["status"] == "succeeded"
    assert result["avantio"]["interval_hours"] == 6
    assert result["avantio"]["row_count"] == 14
