"""O webhook do Mercado Livre precisa responder 200 — sempre, e rápido.

O provedor retenta por 1 hora e desativa o tópico se não receber 200; por isso
os testes cobrem também corpo inválido e requisição de outra aplicação.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from seller_radar.config import Settings
from seller_radar.web.main import create_app


def _client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


def test_notification_is_acknowledged(settings: Settings) -> None:
    with _client(settings) as client:
        response = client.post(
            "/webhooks/mercadolivre",
            json={
                "resource": "/orders/2000001",
                "user_id": 42,
                "topic": "orders_v2",
                "application_id": int(settings.ml_client_id),
                "attempts": 1,
            },
        )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_notification_from_another_application_is_acknowledged(settings: Settings) -> None:
    """Ignorar não é recusar: recusar geraria retentativa e desativaria o tópico."""
    with _client(settings) as client:
        response = client.post(
            "/webhooks/mercadolivre",
            json={"topic": "items", "application_id": 999999999, "resource": "/items/MLB1"},
        )

    assert response.status_code == 200


def test_malformed_body_is_acknowledged(settings: Settings) -> None:
    with _client(settings) as client:
        response = client.post(
            "/webhooks/mercadolivre",
            content=b"nao-e-json",
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 200


def test_webhook_does_not_accept_get(settings: Settings) -> None:
    with _client(settings) as client:
        assert client.get("/webhooks/mercadolivre").status_code == 405


def test_webhook_needs_no_session(settings: Settings) -> None:
    """Sem cookie de sessão e sem CSRF: quem chama é o Mercado Livre."""
    with _client(settings) as client:
        response = client.post("/webhooks/mercadolivre", json={"topic": "items"})

    assert response.status_code == 200
