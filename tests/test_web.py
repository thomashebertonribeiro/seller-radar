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


def test_csp_allows_only_the_mercadolivre_authorization_redirect(settings: Settings) -> None:
    """`form-action` vale também para o redirecionamento do POST /start.

    Sem a origem do Mercado Livre na lista, o navegador cancela o salto para a
    tela de autorização sem mostrar erro — o botão parece não fazer nada.
    """
    with _client(settings) as client:
        csp = client.get("/login").headers["content-security-policy"]

    assert "form-action 'self' https://auth.mercadolivre.com.br" in csp
    assert "form-action *" not in csp
    assert "api.mercadolibre.com" not in csp


def test_no_documentation_endpoints_are_exposed(settings: Settings) -> None:
    with _client(settings) as client:
        for path in ("/docs", "/openapi.json", "/redoc"):
            assert client.get(path).status_code == 404


def test_oauth_start_requires_session(settings: Settings) -> None:
    with _client(settings) as client:
        response = client.post("/auth/mercadolivre/start")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_oauth_callback_requires_session(settings: Settings) -> None:
    with _client(settings) as client:
        response = client.get("/auth/mercadolivre/callback?code=x&state=y")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_oauth_callback_without_code_is_refused(settings: Settings) -> None:
    """Sem sessão nem banco tocado: o retorno incompleto nunca vira conexão."""
    with _client(settings) as client:
        response = client.get("/auth/mercadolivre/callback", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
