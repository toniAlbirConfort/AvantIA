from __future__ import annotations

import base64
import re
from urllib.parse import urlsplit

import httpx

from app.config import Settings, get_settings

DEFAULT_GOWA_DEVICE_ID = "avantia"
GOWA_TIMEOUT_SECONDS = 12


class GowaNotConfigured(Exception):
    pass


class GowaDeviceNotRegistered(Exception):
    pass


class GowaServiceUnavailable(Exception):
    pass


def gowa_device_id(settings: Settings | None = None) -> str:
    configured_id = (settings or get_settings()).gowa_device_id.strip()
    return configured_id or DEFAULT_GOWA_DEVICE_ID


def gowa_is_configured(settings: Settings | None = None) -> bool:
    current = settings or get_settings()
    username, separator, password = current.gowa_basic_auth.partition(":")
    return bool(
        separator
        and username.strip()
        and password
        and current.gowa_webhook_secret
        and current.gowa_url.strip()
    )


def _credentials(settings: Settings) -> tuple[str, str, str]:
    username, separator, password = settings.gowa_basic_auth.partition(":")
    device_id = gowa_device_id(settings)
    if not separator or not username.strip() or not password or not settings.gowa_webhook_secret:
        raise GowaNotConfigured
    return username.strip(), password, device_id


def _client(settings: Settings, username: str, password: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.gowa_url.strip().rstrip("/"),
        timeout=GOWA_TIMEOUT_SECONDS,
        auth=(username, password),
    )


def _json(response: httpx.Response) -> dict:
    try:
        value = response.json()
    except ValueError as exc:
        raise GowaServiceUnavailable from exc
    if not isinstance(value, dict):
        raise GowaServiceUnavailable
    return value


async def _request_json(client: httpx.AsyncClient, method: str, path: str, **kwargs) -> dict:
    try:
        response = await client.request(method, path, **kwargs)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise GowaServiceUnavailable from exc
    return _json(response)


def _device_is_registered(payload: dict, device_id: str) -> bool:
    if "results" not in payload:
        raise GowaServiceUnavailable
    devices = payload["results"]
    if devices is None and payload.get("code") in (None, "SUCCESS"):
        # GOWA v9.0.0 serializes its empty device registry as `results: null`.
        return False
    if not isinstance(devices, list):
        raise GowaServiceUnavailable
    return any(
        isinstance(device, dict)
        and str(device.get("device") or device.get("id") or "") == device_id
        for device in devices
    )


async def _registered_devices(client: httpx.AsyncClient, device_id: str) -> bool:
    payload = await _request_json(client, "GET", "/devices")
    return _device_is_registered(payload, device_id)


async def _ensure_device(client: httpx.AsyncClient, device_id: str) -> None:
    if await _registered_devices(client, device_id):
        return
    try:
        await _request_json(client, "POST", "/devices", json={"device_id": device_id})
    except GowaServiceUnavailable:
        # Another admin may have created the single device slot concurrently.
        if await _registered_devices(client, device_id):
            return
        raise


async def get_gowa_status(settings: Settings | None = None) -> dict:
    current = settings or get_settings()
    username, password, device_id = _credentials(current)
    try:
        async with _client(current, username, password) as client:
            registered = await _registered_devices(client, device_id)
            if not registered:
                return {
                    "configured": True,
                    "service_available": True,
                    "device_id": device_id,
                    "registered": False,
                    "is_connected": False,
                    "is_logged_in": False,
                }
            payload = await _request_json(
                client, "GET", "/app/status", headers={"X-Device-Id": device_id}
            )
    except httpx.HTTPError as exc:
        raise GowaServiceUnavailable from exc

    results = payload.get("results")
    if not isinstance(results, dict):
        raise GowaServiceUnavailable
    return {
        "configured": True,
        "service_available": True,
        "device_id": device_id,
        "registered": True,
        "is_connected": bool(results.get("is_connected")),
        "is_logged_in": bool(results.get("is_logged_in")),
    }


async def start_gowa_pairing(settings: Settings | None = None) -> dict:
    current = settings or get_settings()
    username, password, device_id = _credentials(current)
    try:
        async with _client(current, username, password) as client:
            await _ensure_device(client, device_id)
            payload = await _request_json(
                client, "GET", "/app/login", headers={"X-Device-Id": device_id}
            )
            results = payload.get("results")
            if not isinstance(results, dict):
                raise GowaServiceUnavailable
            qr_link = results.get("qr_link")
            if not isinstance(qr_link, str):
                raise GowaServiceUnavailable

            # Only fetch GOWA's QR static files from the configured GOWA host.
            # Never trust the host returned in qr_link or allow redirects.
            qr_path = urlsplit(qr_link).path
            static_prefix = next(
                (
                    prefix
                    for prefix in ("/statics/qrcode/", "/statics/images/qrcode/")
                    if qr_path.startswith(prefix)
                ),
                None,
            )
            if static_prefix is None:
                raise GowaServiceUnavailable
            filename = qr_path.removeprefix(static_prefix)
            if not re.fullmatch(r"[A-Za-z0-9_-]+\.png", filename):
                raise GowaServiceUnavailable
            try:
                image_response = await client.get(qr_path, follow_redirects=False)
                image_response.raise_for_status()
            except httpx.HTTPError as exc:
                raise GowaServiceUnavailable from exc
            content_type = image_response.headers.get("content-type", "image/png").split(";")[0]
            if content_type.lower() != "image/png" or not image_response.content:
                raise GowaServiceUnavailable

            try:
                qr_duration = min(300, max(1, int(results.get("qr_duration", 30))))
            except (TypeError, ValueError):
                qr_duration = 30
            return {
                "qr_image": (
                    "data:image/png;base64,"
                    + base64.b64encode(image_response.content).decode("ascii")
                ),
                "qr_duration": qr_duration,
            }
    except httpx.HTTPError as exc:
        raise GowaServiceUnavailable from exc


async def reconnect_gowa(settings: Settings | None = None) -> None:
    current = settings or get_settings()
    username, password, device_id = _credentials(current)
    try:
        async with _client(current, username, password) as client:
            if not await _registered_devices(client, device_id):
                raise GowaDeviceNotRegistered
            await _request_json(
                client, "GET", "/app/reconnect", headers={"X-Device-Id": device_id}
            )
    except httpx.HTTPError as exc:
        raise GowaServiceUnavailable from exc
