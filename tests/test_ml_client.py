"""A invariante que define o projeto: nada além de GET sai para o Mercado Livre."""

from __future__ import annotations

import httpx
import pytest

from seller_radar.ml.client import MercadoLivreClient, MercadoLivreError, ReadOnlyViolation
from tests.conftest import make_transport


def _client(transport: httpx.MockTransport) -> MercadoLivreClient:
    return MercadoLivreClient("token-de-teste", transport=transport, max_attempts=3)


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def test_rejects_every_method_but_get(method: str) -> None:
    captured: list[httpx.Request] = []
    client = _client(make_transport([httpx.Response(200, json={})], captured))

    with pytest.raises(ReadOnlyViolation):
        await client._request(method, "/items/MLB123")

    assert captured == [], "a requisição não pode nem chegar ao transporte"
    await client.aclose()


async def test_get_reaches_transport_and_returns_json() -> None:
    captured: list[httpx.Request] = []
    client = _client(make_transport([httpx.Response(200, json={"id": "MLB123"})], captured))

    payload = await client.get_json("/items/MLB123")

    assert payload == {"id": "MLB123"}
    assert captured[0].method == "GET"
    assert str(captured[0].url) == "https://api.mercadolibre.com/items/MLB123"
    await client.aclose()


async def test_retries_on_429_and_honours_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MercadoLivreClient, "_backoff", staticmethod(lambda attempt, response: 0))
    captured: list[httpx.Request] = []
    client = _client(
        make_transport(
            [
                httpx.Response(429, headers={"Retry-After": "1"}),
                httpx.Response(200, json={"ok": True}),
            ],
            captured,
        )
    )

    assert await client.get_json("/users/me") == {"ok": True}
    assert len(captured) == 2
    await client.aclose()


async def test_definitive_4xx_is_not_retried() -> None:
    captured: list[httpx.Request] = []
    client = _client(make_transport([httpx.Response(403, text="forbidden")], captured))

    with pytest.raises(MercadoLivreError) as exc:
        await client.get_json("/users/me")

    assert exc.value.status_code == 403
    assert len(captured) == 1
    await client.aclose()


async def test_gives_up_after_max_attempts_on_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MercadoLivreClient, "_backoff", staticmethod(lambda attempt, response: 0))
    captured: list[httpx.Request] = []
    client = _client(
        make_transport([httpx.Response(500, text="boom") for _ in range(3)], captured)
    )

    with pytest.raises(MercadoLivreError):
        await client.get_json("/users/me")

    assert len(captured) == 3
    await client.aclose()
