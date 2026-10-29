from __future__ import annotations

import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import gowa
from app.auth import hasher
from app.config import get_settings
from app.db import get_db
from app.main import app
from app.models import User


def _users(db):
    admin = User(
        username="gowa-admin",
        password_hash=hasher.hash("very-long-admin-password"),
        role="admin",
    )
    operator = User(
        username="gowa-operator",
        password_hash=hasher.hash("very-long-operator-password"),
        role="operator",
    )
    db.add_all([admin, operator])
    db.commit()


def _configure(db, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_secret", "unit-test-secret-with-more-than-32-characters")
    monkeypatch.setattr(settings, "gowa_basic_auth", "api-user:api-password")
    monkeypatch.setattr(settings, "gowa_webhook_secret", "webhook-test-secret")
    monkeypatch.setattr(settings, "gowa_device_id", "custom-device")
    app.dependency_overrides[get_db] = lambda: db
    _users(db)


def _tokens(client: TestClient) -> tuple[dict[str, str], dict[str, str]]:
    admin_login = client.post(
        "/api/login", json={"username": "gowa-admin", "password": "very-long-admin-password"}
    )
    operator_login = client.post(
        "/api/login",
        json={"username": "gowa-operator", "password": "very-long-operator-password"},
    )
    return (
        {"Authorization": f"Bearer {admin_login.json()['token']}"},
        {"Authorization": f"Bearer {operator_login.json()['token']}"},
    )


def _mock_gowa(monkeypatch, handler):
    original_client = httpx.AsyncClient

    def make_client(settings, username, password):
        return original_client(
            base_url=settings.gowa_url.rstrip("/"),
            timeout=gowa.GOWA_TIMEOUT_SECONDS,
            auth=(username, password),
            transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr(gowa, "_client", make_client)


def test_gowa_management_is_admin_only_and_not_listed_to_operators(db, monkeypatch):
    _configure(db, monkeypatch)
    try:
        with TestClient(app) as client:
            admin_auth, operator_auth = _tokens(client)
            assert client.get("/api/integrations/gowa").status_code == 401
            assert client.get("/api/integrations/gowa", headers=operator_auth).status_code == 403
            assert (
                client.post("/api/integrations/gowa/connect", headers=operator_auth).status_code
                == 403
            )
            assert (
                client.post("/api/integrations/gowa/reconnect", headers=operator_auth).status_code
                == 403
            )
            assert "whatsapp" not in client.get("/api/integrations", headers=operator_auth).json()
            assert "whatsapp" in client.get("/api/integrations", headers=admin_auth).json()
    finally:
        app.dependency_overrides.clear()


def test_gowa_status_reads_connected_state(db, monkeypatch):
    _configure(db, monkeypatch)
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/devices":
            return httpx.Response(200, json={"results": [{"device": "custom-device"}]})
        if request.url.path == "/app/status":
            return httpx.Response(
                200,
                json={"results": {"is_connected": True, "is_logged_in": True}},
            )
        return httpx.Response(404)

    _mock_gowa(monkeypatch, handler)
    try:
        with TestClient(app) as client:
            admin_auth, _ = _tokens(client)
            response = client.get("/api/integrations/gowa", headers=admin_auth)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            assert response.json() == {
                "configured": True,
                "service_available": True,
                "device_id": "custom-device",
                "registered": True,
                "is_connected": True,
                "is_logged_in": True,
            }
        assert [request.url.path for request in requests] == ["/devices", "/app/status"]
        assert all(
            request.headers["authorization"] == "Basic YXBpLXVzZXI6YXBpLXBhc3N3b3Jk"
            for request in requests
        )
        assert requests[-1].headers["X-Device-Id"] == "custom-device"
    finally:
        app.dependency_overrides.clear()


def test_gowa_status_reports_registered_but_disconnected_device(db, monkeypatch):
    _configure(db, monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/devices":
            return httpx.Response(200, json={"results": [{"device": "custom-device"}]})
        if request.url.path == "/app/status":
            return httpx.Response(
                200,
                json={"results": {"is_connected": False, "is_logged_in": False}},
            )
        return httpx.Response(404)

    _mock_gowa(monkeypatch, handler)
    try:
        with TestClient(app) as client:
            admin_auth, _ = _tokens(client)
            response = client.get("/api/integrations/gowa", headers=admin_auth)
            assert response.status_code == 200
            assert response.json()["registered"] is True
            assert response.json()["is_connected"] is False
            assert response.json()["is_logged_in"] is False
    finally:
        app.dependency_overrides.clear()


def test_gowa_status_accepts_empty_device_registry(db, monkeypatch):
    _configure(db, monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/devices":
            return httpx.Response(200, json={"code": "SUCCESS", "results": None})
        return httpx.Response(404)

    _mock_gowa(monkeypatch, handler)
    try:
        with TestClient(app) as client:
            admin_auth, _ = _tokens(client)
            response = client.get("/api/integrations/gowa", headers=admin_auth)
            assert response.status_code == 200
            assert response.json()["registered"] is False
            assert response.json()["is_connected"] is False
            assert response.json()["is_logged_in"] is False
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "qr_path",
    ["/statics/qrcode/current.png", "/statics/images/qrcode/current.png"],
)
def test_gowa_connect_creates_device_and_proxies_qr_without_trusting_returned_host(
    db, monkeypatch, qr_path
):
    _configure(db, monkeypatch)
    requests = []
    qr_png = b"\x89PNG\r\n\x1a\nqr-image"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/devices" and request.method == "GET":
            return httpx.Response(200, json={"code": "SUCCESS", "results": None})
        if request.url.path == "/devices" and request.method == "POST":
            assert json.loads(request.content) == {"device_id": "custom-device"}
            return httpx.Response(200, json={"results": {"id": "custom-device"}})
        if request.url.path == "/app/login":
            assert request.headers["X-Device-Id"] == "custom-device"
            return httpx.Response(
                200,
                json={
                    "results": {
                        "qr_duration": 30,
                        "qr_link": f"http://untrusted.example{qr_path}",
                    }
                },
            )
        if request.url.path == qr_path:
            assert request.url.host == "gowa"
            return httpx.Response(200, content=qr_png, headers={"content-type": "image/png"})
        return httpx.Response(404)

    _mock_gowa(monkeypatch, handler)
    try:
        with TestClient(app) as client:
            admin_auth, _ = _tokens(client)
            response = client.post("/api/integrations/gowa/connect", headers=admin_auth)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            result = response.json()
            expected_image = "data:image/png;base64," + base64.b64encode(qr_png).decode()
            assert result["qr_image"] == expected_image
            assert result["qr_duration"] == 30
            assert "api-password" not in response.text
            assert [request.url.path for request in requests] == [
                "/devices",
                "/devices",
                "/app/login",
                qr_path,
            ]
    finally:
        app.dependency_overrides.clear()


def test_gowa_reconnect_and_service_errors(db, monkeypatch):
    _configure(db, monkeypatch)
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/devices":
            return httpx.Response(200, json={"results": [{"device": "custom-device"}]})
        if request.url.path == "/app/reconnect":
            assert request.headers["X-Device-Id"] == "custom-device"
            return httpx.Response(200, json={"code": "SUCCESS", "results": None})
        return httpx.Response(404)

    _mock_gowa(monkeypatch, handler)
    try:
        with TestClient(app) as client:
            admin_auth, _ = _tokens(client)
            response = client.post("/api/integrations/gowa/reconnect", headers=admin_auth)
            assert response.status_code == 200
            assert response.json() == {"status": "reconnecting"}
            assert [request.url.path for request in requests] == ["/devices", "/app/reconnect"]

            def broken_handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(500)

            _mock_gowa(monkeypatch, broken_handler)
            failure = client.get("/api/integrations/gowa", headers=admin_auth)
            assert failure.status_code == 503
            assert failure.json()["detail"] == "No se puede consultar el servicio GOWA"
    finally:
        app.dependency_overrides.clear()
