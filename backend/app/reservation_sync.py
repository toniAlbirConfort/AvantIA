from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.models import ReservationSyncState, now
from app.planning import MAX_BYTES, commit_import

logger = logging.getLogger(__name__)
SYNC_STATE_KEY = "avantio_reservations"
MAX_DOWNLOAD_BYTES = MAX_BYTES
NAVIGATION_TIMEOUT_MS = 45_000
DOWNLOAD_TIMEOUT_MS = 60_000


class ReservationSyncError(Exception):
    """An expected, safe-to-display reservation synchronization failure."""


def is_configured(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    # Authentication now comes from the dedicated, logged-in Windows Chrome profile.
    return bool(settings.avantio_export_url.strip())


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def sync_is_due(
    last_attempt_at: datetime | None,
    interval_hours: int,
    current_time: datetime | None = None,
) -> bool:
    if last_attempt_at is None:
        return True
    current_time = current_time or now()
    return _utc(current_time) - _utc(last_attempt_at) >= timedelta(hours=interval_hours)


def storage_state_updated_since(
    last_attempt_at: datetime | None,
    storage_state_path: str | Path,
) -> bool:
    if last_attempt_at is None:
        return False
    try:
        return Path(storage_state_path).stat().st_mtime > _utc(last_attempt_at).timestamp()
    except OSError:
        return False


def _claim_if_due(
    settings: Settings,
    session_factory: Callable[[], Session] = SessionLocal,
    current_time: datetime | None = None,
) -> bool:
    if not is_configured(settings):
        return False
    current_time = current_time or now()
    try:
        with session_factory() as db:
            state = db.scalar(
                select(ReservationSyncState)
                .where(ReservationSyncState.key == SYNC_STATE_KEY)
                .with_for_update()
            )
            if state is not None:
                due = sync_is_due(
                    state.last_attempt_at, settings.avantio_sync_interval_hours, current_time
                )
                state_changed = storage_state_updated_since(
                    state.last_attempt_at, settings.avantio_storage_state_path
                )
                if not due and not state_changed:
                    return False
            if state is None:
                state = ReservationSyncState(key=SYNC_STATE_KEY)
                db.add(state)
            state.status = "running"
            state.last_attempt_at = current_time
            state.last_error = None
            db.commit()
        return True
    except IntegrityError:
        # Another worker created the singleton state row at the same time.
        return False


async def _visible_locator(page, selectors: tuple[str, ...]):
    for selector in selectors:
        locator = page.locator(selector)
        for index in range(await locator.count()):
            candidate = locator.nth(index)
            if await candidate.is_visible():
                return candidate
    return None


async def _click_label(page, label: str, roles: tuple[str, ...]) -> None:
    for role in roles:
        locator = page.get_by_role(role, name=label, exact=True)
        visible = []
        for index in range(await locator.count()):
            candidate = locator.nth(index)
            if await candidate.is_visible():
                visible.append(candidate)
        if len(visible) == 1:
            await visible[0].click()
            return
        if len(visible) > 1:
            raise ReservationSyncError(f"El control «{label}» aparece más de una vez.")

    locator = page.get_by_text(label, exact=True)
    visible = []
    for index in range(await locator.count()):
        candidate = locator.nth(index)
        if await candidate.is_visible():
            visible.append(candidate)
    if len(visible) == 1:
        await visible[0].click()
        return
    if len(visible) > 1:
        raise ReservationSyncError(f"El control «{label}» aparece más de una vez.")
    raise ReservationSyncError(f"No se encontró el control «{label}» en Avantio.")


async def _login_if_needed(page, settings: Settings) -> None:
    password = await _visible_locator(page, ('input[type="password"]',))
    if password is None:
        return

    username = await _visible_locator(
        page,
        (
            'input[autocomplete="username"]',
            'input[type="email"]',
            'input[name*="user" i]',
            'input[name*="usu" i]',
            'input[name*="login" i]',
            'input[type="text"]',
        ),
    )
    if username is None:
        raise ReservationSyncError("No se encontró el campo de usuario de Avantio.")
    await username.fill(settings.avantio_username)
    await password.fill(settings.avantio_password)

    submit = await _visible_locator(page, ('button[type="submit"]', 'input[type="submit"]'))
    if submit is None:
        submit = await _visible_locator(
            page,
            ('button:has-text("Entrar")', 'button:has-text("Acceder")', 'button:has-text("Login")'),
        )
    if submit is None:
        raise ReservationSyncError("No se encontró el botón de acceso de Avantio.")
    await submit.click()
    try:
        await page.locator('input[type="password"]').wait_for(state="hidden", timeout=20_000)
    except PlaywrightTimeoutError as exc:
        raise ReservationSyncError(
            "Avantio rechazó el acceso con las credenciales configuradas."
        ) from exc


async def _open_navigation_menu(page) -> None:
    menu_button = await _visible_locator(
        page,
        (
            'button[aria-label*="menu" i]',
            'button[aria-label*="menú" i]',
            'button[aria-label*="navigation" i]',
            'button[aria-label*="navegación" i]',
            'button[title*="menu" i]',
            'button[title*="menú" i]',
            'button[title*="navigation" i]',
            'button.navbar-toggler',
            'button.sidebar-toggle',
            'button:has(.fa-bars)',
            '[role="button"]:has(.fa-bars)',
            '.fa-bars',
        ),
    )
    if menu_button is None:
        raise ReservationSyncError("No se encontró el menú de navegación de Avantio.")
    await menu_button.click()


async def download_reservations_csv(settings: Settings) -> tuple[str, bytes]:
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=False)
            try:
                context_options: dict[str, object] = {"accept_downloads": True}
                storage_state_path = Path(settings.avantio_storage_state_path)
                if storage_state_path.is_file():
                    context_options["storage_state"] = str(storage_state_path)
                context = await browser.new_context(**context_options)
                page = await context.new_page()
                page.set_default_timeout(15_000)
                avantio_url = urlsplit(settings.avantio_export_url)
                home_url = f"{avantio_url.scheme}://{avantio_url.netloc}/"
                response = await page.goto(
                    home_url,
                    wait_until="domcontentloaded",
                    timeout=NAVIGATION_TIMEOUT_MS,
                )
                if response is not None and response.status == 403:
                    raise ReservationSyncError(
                        "Avantio bloque la petición automatizada con HTTP 403 antes del login."
                    )
                await _login_if_needed(page, settings)
                response = await page.goto(
                    home_url,
                    wait_until="domcontentloaded",
                    timeout=NAVIGATION_TIMEOUT_MS,
                )
                if response is not None and response.status == 403:
                    raise ReservationSyncError(
                        "Avantio bloque la petición automatizada con HTTP 403 antes del login."
                    )
                if await _visible_locator(page, ('input[type="password"]',)) is not None:
                    raise ReservationSyncError(
                        "Avantio rechazó el acceso con las credenciales configuradas."
                    )

                await _open_navigation_menu(page)
                await _click_label(page, "Reservas", ("menuitem", "link", "button"))
                await _click_label(
                    page,
                    "Listado de reservas",
                    ("menuitem", "link", "button"),
                )
                await _click_label(page, "Más acciones", ("button", "link"))
                async with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as download_info:
                    await _click_label(page, "Descargar CSV", ("menuitem", "button", "link"))
                download = await download_info.value
                if await download.failure():
                    raise ReservationSyncError("Avantio no completó la descarga del CSV.")

                filename = download.suggested_filename.replace("\\", "/").rsplit("/", 1)[-1]
                if not filename.lower().endswith(".csv"):
                    raise ReservationSyncError("Avantio devolvió un archivo que no es CSV.")
                download_path = await download.path()
                if not download_path:
                    raise ReservationSyncError("No se pudo acceder al CSV descargado.")
                path = Path(download_path)
                if path.stat().st_size > MAX_DOWNLOAD_BYTES:
                    raise ReservationSyncError("El CSV supera el límite de 10 MB.")
                return filename[:255], path.read_bytes()
            finally:
                await browser.close()
    except ReservationSyncError:
        raise
    except PlaywrightTimeoutError as exc:
        raise ReservationSyncError("Avantio no respondió antes del tiempo límite.") from exc
    except PlaywrightError as exc:
        raise ReservationSyncError("No se pudo completar la exportación en Avantio.") from exc


def _save_failure(
    message: str,
    session_factory: Callable[[], Session],
) -> None:
    with session_factory() as db:
        state = db.get(ReservationSyncState, SYNC_STATE_KEY)
        if state is None:
            state = ReservationSyncState(key=SYNC_STATE_KEY)
            db.add(state)
        state.status = "failed"
        state.last_error = message[:500]
        db.commit()


async def sync_reservations_if_due(
    settings: Settings | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
    current_time: datetime | None = None,
) -> bool:
    settings = settings or get_settings()
    try:
        claimed = _claim_if_due(settings, session_factory, current_time)
    except Exception as exc:
        message = f"No se pudo iniciar la sincronización ({type(exc).__name__})."
        try:
            _save_failure(message, session_factory)
        except Exception as state_exc:
            logger.error(
                "Could not persist Avantio sync failure (%s).", type(state_exc).__name__
            )
        logger.error("Avantio reservation sync failed: %s", message)
        return False
    if not claimed:
        return False

    try:
        filename, raw = await download_reservations_csv(settings)
        checksum = hashlib.sha256(raw).hexdigest()
        with session_factory() as db:
            batch = commit_import(
                db,
                dataset="reservations",
                raw=raw,
                filename=filename,
                actor_user_id=None,
                expected_checksum=checksum,
                source="playwright",
            )
            state = db.get(ReservationSyncState, SYNC_STATE_KEY)
            if state is None:
                raise RuntimeError("No se encontró el estado de sincronización.")
            if batch.status != "valid":
                state.status = "failed"
                state.last_error = (batch.error or "El CSV no pasó la validación.")[:500]
            else:
                state.status = "succeeded"
                state.last_success_at = current_time or now()
                state.last_batch_id = batch.id
                state.row_count = batch.row_count
                state.last_error = None
            db.commit()
            succeeded = batch.status == "valid"
    except ReservationSyncError as exc:
        try:
            _save_failure(str(exc), session_factory)
        except Exception as state_exc:
            logger.error("Could not persist Avantio sync failure (%s).", type(state_exc).__name__)
        logger.warning("Avantio reservation sync failed: %s", str(exc))
        return True
    except Exception as exc:
        safe_message = f"Error interno de sincronización ({type(exc).__name__})."
        try:
            _save_failure(safe_message, session_factory)
        except Exception as state_exc:
            logger.error("Could not persist Avantio sync failure (%s).", type(state_exc).__name__)
        logger.error("Avantio reservation sync failed: %s", safe_message)
        return True

    if not succeeded:
        logger.warning("Avantio reservation sync failed: the downloaded CSV was rejected.")
    return True
