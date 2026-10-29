from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from seller_radar.config import Settings
from seller_radar.ml.oauth import (
    SCOPE,
    TOKEN_URL,
    OAuthError,
    build_authorize_url,
    exchange_code,
    refresh,
)
from tests.conftest import make_transport


def test_authorize_url_carries_state_scope_and_redirect(settings: Settings) -> None:
    url = build_authorize_url(settings, "estado-unico")

    assert url.startswith("https://auth.mercadolivre.com.br/authorization?")
    assert "response_type=code" in url
    assert "client_id=123456" in url
    assert "state=estado-unico" in url
    assert f"scope={SCOPE.replace(' ', '+')}" in url
    assert "redirect_uri=https%3A%2F%2Fml.exemplo.com%2Fcallback" in url


def test_authorize_url_requires_configuration(settings: Settings) -> None:
    sem_ml = settings.model_copy(update={"ml_client_id": ""})

    with pytest.raises(OAuthError):
        build_authorize_url(sem_ml, "estado")


async def test_exchange_code_posts_only_to_token_endpoint(settings: Settings) -> None:
    captured: list[httpx.Request] = []
    transport = make_transport(
        [
            httpx.Response(
                200,
                json={
                    "access_token": "APP_USR-1",
                    "refresh_token": "TG-1",
                    "expires_in": 21600,
                    "scope": "offline_access read",
                    "user_id": 42,
                },
            )
        ],
        captured,
    )

    tokens = await exchange_code(settings, "codigo-de-teste", transport=transport)

    assert str(captured[0].url) == TOKEN_URL
    assert captured[0].method == "POST"
    body = captured[0].content.decode()
    assert "grant_type=authorization_code" in body
    assert "code=codigo-de-teste" in body
    assert "client_secret=segredo" in body
    assert tokens.access_token == "APP_USR-1"
    assert tokens.ml_user_id == "42"
    assert tokens.expires_at > datetime.now(UTC)


async def test_refresh_uses_refresh_token_grant(settings: Settings) -> None:
    captured: list[httpx.Request] = []
    transport = make_transport(
        [
            httpx.Response(
                200,
                json={
                    "access_token": "APP_USR-2",
                    "refresh_token": "TG-2",
                    "expires_in": 21600,
                },
            )
        ],
        captured,
    )

    tokens = await refresh(settings, "TG-1", transport=transport)

    assert "grant_type=refresh_token" in captured[0].content.decode()
    assert tokens.refresh_token == "TG-2"
    assert tokens.ml_user_id is None


async def test_invalid_grant_is_surfaced(settings: Settings) -> None:
    transport = make_transport(
        [
            httpx.Response(
                400,
                json={"error": "invalid_grant", "message": "refresh token expirado"},
            )
        ]
    )

    with pytest.raises(OAuthError) as exc:
        await refresh(settings, "TG-velho", transport=transport)

    assert exc.value.error == "invalid_grant"
    assert "expirado" in str(exc.value)
