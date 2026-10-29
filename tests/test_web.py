"""Testes de fumaça da interface: nada sensível sem sessão, nada sem CSRF."""

from __future__ import annotations

from fastapi.testclient import TestClient

from seller_radar.config import Settings
from seller_radar.web.main import create_app, reset_login_throttle


def _client(settings: Settings) -> TestClient:
    reset_login_throttle()
    return TestClient(create_app(settings), follow_redirects=False)


def test_healthz(settings: Settings) -> None:
    with _client(settings) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_dashboard_requires_session(settings: Settings) -> None:
    with _client(settings) as client:
        response = client.get("/")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_page_sets_csrf_token_in_session(settings: Settings) -> None:
    with _client(settings) as client:
        response = client.get("/login")

        assert response.status_code == 200
        assert 'name="csrf_token"' in response.text
        assert client.cookies.get("radar_session")


def test_login_without_csrf_is_rejected(settings: Settings) -> None:
    with _client(settings) as client:
        client.get("/login")
        response = client.post("/login", data={"email": "a@b.com", "password": "x"})

    assert response.status_code == 400
    assert "Sessão expirada" in response.text


def test_security_headers_are_present(settings: Settings) -> None:
    with _client(settings) as client:
        response = client.get("/login")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_no_documentation_endpoints_are_exposed(settings: Settings) -> None:
    with _client(settings) as client:
        for path in ("/docs", "/openapi.json", "/redoc"):
            assert client.get(path).status_code == 404
