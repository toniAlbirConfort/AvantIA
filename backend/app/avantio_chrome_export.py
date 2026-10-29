from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

CDP_ENDPOINT = "http://127.0.0.1:9222"
HOME_URL = "https://app.avantio.pro/"
MAX_DOWNLOAD_BYTES = 10_000_000


class ExportError(Exception):
    pass


def _resolve_output(output: Path) -> Path:
    import_root_value = os.environ.get("AVANTIO_IMPORT_DIR", "").strip()
    if not import_root_value:
        raise ExportError("AVANTIO_IMPORT_DIR no está configurada en la tarea del servidor.")
    import_root = Path(import_root_value).resolve()
    resolved = output.resolve()
    if (
        resolved.parent != import_root
        or not resolved.name.startswith("avantio-")
        or resolved.suffix.lower() != ".csv"
    ):
        raise ExportError("La ruta temporal del CSV está fuera de la carpeta autorizada.")
    return resolved


async def _click_visible_label(page, label: str, roles: tuple[str, ...]) -> None:
    if label == "Más acciones":
        locator = page.get_by_text(label, exact=True)
        for _ in range(40):
            visible = False
            for index in range(await locator.count()):
                if await locator.nth(index).is_visible():
                    visible = True
                    break
            if visible:
                break
            await page.wait_for_timeout(250)

    for role in roles:
        locator = page.get_by_role(role, name=label, exact=True)
        visible = []
        for index in range(await locator.count()):
            candidate = locator.nth(index)
            if await candidate.is_visible():
                visible.append(candidate)
        if visible:
            if label in ("Reservas", "Más acciones") and len(visible) > 1:
                # The navigation item and toolbar action are above duplicate dashboard/row labels.
                candidates = []
                for candidate in visible:
                    bounds = await candidate.bounding_box()
                    if bounds is not None:
                        candidates.append((bounds["y"], candidate))
                if candidates:
                    visible = [min(candidates, key=lambda item: item[0])[1]]
            await visible[0].click()
            return

    locator = page.get_by_text(label, exact=True)
    visible = []
    for index in range(await locator.count()):
        candidate = locator.nth(index)
        if await candidate.is_visible():
            visible.append(candidate)
    if label in ("Reservas", "Más acciones") and len(visible) > 1:
        # These text-only controls can be duplicated by dashboard and row content.
        candidates = []
        for candidate in visible:
            bounds = await candidate.bounding_box()
            if bounds is not None:
                candidates.append((bounds["y"], candidate))
        if candidates:
            visible = [min(candidates, key=lambda item: item[0])[1]]
    if len(visible) == 1:
        await visible[0].click()
        return
    raise ExportError(f"No se encontró el control «{label}» en Avantio.")


async def _open_menu(page) -> None:
    selectors = (
        'button[aria-label*="menu" i]',
        'button[aria-label*="menú" i]',
        'button[aria-label*="navigation" i]',
        'button[aria-label*="navegación" i]',
        'button[title*="menu" i]',
        'button[title*="menú" i]',
        'button.navbar-toggler',
        'button.sidebar-toggle',
        'button:has(.fa-bars)',
        '[role="button"]:has(.fa-bars)',
        'div.tabBarraSuperior.flexBarraSuperior',
        '.fa-bars',
    )
    for selector in selectors:
        locator = page.locator(selector)
        for index in range(await locator.count()):
            candidate = locator.nth(index)
            if await candidate.is_visible():
                await candidate.click()
                return
    raise ExportError("No se encontró el menú de navegación de Avantio.")


async def _login_form_visible(page) -> bool:
    passwords = page.locator('input[type="password"]')
    for index in range(await passwords.count()):
        if await passwords.nth(index).is_visible():
            return True
    return False


async def _download(page, browser, destination: Path) -> None:
    import_root = destination.parent.resolve()
    download_dir: Path | None = None
    cdp = None
    completed_files: list[str] = []

    def track_progress(event: dict) -> None:
        if event.get("state") == "completed" and event.get("filePath"):
            completed_files.append(str(event["filePath"]))

    try:
        cdp = await browser.new_browser_cdp_session()
        download_dir = Path(tempfile.mkdtemp(prefix="avantio-download-", dir=destination.parent))
        cdp.on("Browser.downloadProgress", track_progress)
        await cdp.send(
            "Browser.setDownloadBehavior",
            {
                "behavior": "allow",
                "downloadPath": str(download_dir),
                "eventsEnabled": True,
            },
        )
        async with page.expect_download(timeout=60_000) as download_info:
            await _click_visible_label(page, "Descargar CSV", ("menuitem", "button", "link"))
        download = await download_info.value
        if await download.failure():
            raise ExportError("Avantio no completó la descarga del CSV.")
        if not download.suggested_filename.lower().endswith(".csv"):
            raise ExportError("Avantio devolvió un archivo que no es CSV.")

        source: Path | None = None
        for _ in range(50):
            for file_path in reversed(completed_files):
                candidate = Path(file_path).resolve()
                try:
                    candidate.relative_to(download_dir.resolve())
                except ValueError:
                    continue
                if candidate.is_file():
                    source = candidate
                    break
            if source is None and download_dir is not None:
                candidates = [
                    path
                    for path in download_dir.iterdir()
                    if path.is_file() and path.suffix.lower() == ".csv"
                ]
                if len(candidates) == 1:
                    source = candidates[0]
            if source is not None:
                break
            await asyncio.sleep(0.1)

        if source is not None:
            shutil.copyfile(source, destination)
        else:
            # Playwright can copy the completed download when Chrome reports no CDP filePath.
            await download.save_as(str(destination))
        if not destination.is_file() or destination.stat().st_size == 0:
            raise ExportError("No se pudo guardar el CSV descargado.")
        if destination.stat().st_size > MAX_DOWNLOAD_BYTES:
            raise ExportError("El CSV supera el límite de 10 MB.")
    finally:
        if cdp is not None:
            try:
                await cdp.send("Browser.setDownloadBehavior", {"behavior": "default"})
            except PlaywrightError:
                pass
            try:
                await cdp.detach()
            except PlaywrightError:
                pass
        if download_dir is not None:
            resolved_download_dir = download_dir.resolve()
            if (
                resolved_download_dir.parent == import_root
                and resolved_download_dir.name.startswith("avantio-download-")
            ):
                shutil.rmtree(resolved_download_dir, ignore_errors=True)


async def export_reservations(output: Path) -> None:
    output = _resolve_output(output)
    export_url = os.environ.get("AVANTIO_EXPORT_URL", "").strip()
    parsed_url = urlsplit(export_url)
    if parsed_url.scheme != "https" or parsed_url.hostname != "app.avantio.pro":
        raise ExportError("AVANTIO_EXPORT_URL debe apuntar a https://app.avantio.pro.")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ExportError("El archivo temporal de salida ya existe.")

    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.connect_over_cdp(CDP_ENDPOINT, timeout=10_000)
        except PlaywrightError as exc:
            raise ExportError(
                "Chrome dedicado de Avantio no está disponible en el puerto local 9222."
            ) from exc
        if not browser.contexts:
            raise ExportError("Chrome dedicado no tiene una sesión de navegador abierta.")
        context = browser.contexts[0]
        existing_pages = [
            page
            for page in context.pages
            if urlsplit(page.url).hostname == "app.avantio.pro"
        ]
        close_page_when_done = not existing_pages
        page = existing_pages[0] if existing_pages else await context.new_page()
        page.set_default_timeout(15_000)
        try:
            response = await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60_000)
            if response is not None and response.status == 403:
                raise ExportError("Avantio bloqueó la página con HTTP 403.")
            if await _login_form_visible(page):
                raise ExportError(
                    "La sesión de Avantio ha caducado. Inicia sesión en el Chrome dedicado."
                )

            await _open_menu(page)
            await _click_visible_label(page, "Reservas", ("menuitem", "link", "button"))
            await _click_visible_label(
                page, "Listado de reservas", ("menuitem", "link", "button")
            )

            # Restore the configured 14-day filter after opening the list through its menu.
            response = await page.goto(
                export_url,
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            if response is not None and response.status == 403:
                raise ExportError("Avantio bloqueó el listado con HTTP 403.")
            if await _login_form_visible(page):
                raise ExportError(
                    "La sesión de Avantio ha caducado. Inicia sesión en el Chrome dedicado."
                )

            await _click_visible_label(page, "Más acciones", ("button", "link"))
            await _download(page, browser, output)
        except PlaywrightTimeoutError as exc:
            raise ExportError("Avantio no respondió dentro del tiempo límite.") from exc
        except PlaywrightError as exc:
            raise ExportError("Playwright no pudo completar el flujo de exportación.") from exc
        finally:
            if close_page_when_done:
                try:
                    await page.close()
                except PlaywrightError:
                    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Exporta reservas desde Chrome dedicado")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    safe_output: Path | None = None
    try:
        safe_output = _resolve_output(args.output)
        asyncio.run(export_reservations(safe_output))
        print("Exportación CSV completada.")
        return 0
    except ExportError as exc:
        if safe_output is not None:
            safe_output.unlink(missing_ok=True)
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        if safe_output is not None:
            safe_output.unlink(missing_ok=True)
        print(f"Error inesperado del exportador ({type(exc).__name__}).", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
