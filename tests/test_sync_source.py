"""Paginação e limites da fonte real, com transporte falso.

Nenhuma chamada sai para a internet: o `httpx.MockTransport` responde na ordem e
registra as requisições, então os limites medidos viram teste.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from seller_radar.ml.client import MercadoLivreClient
from seller_radar.sync import source as source_module
from seller_radar.sync.source import MlSource
from tests.conftest import make_transport


def _pair(responses: list[httpx.Response]):
    captured: list[httpx.Request] = []
    client = MercadoLivreClient(
        "token", transport=make_transport(responses, captured), max_attempts=1
    )
    return client, captured


async def test_items_paginate_until_total(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(source_module, "ITEMS_PAGE", 2)
    client, captured = _pair(
        [
            httpx.Response(200, json={"paging": {"total": 3}, "results": ["MLB1", "MLB2"]}),
            httpx.Response(200, json={"paging": {"total": 3}, "results": ["MLB3"]}),
        ]
    )
    source = MlSource(client, "2617251613", spacing=0)

    ids = await source.item_ids()

    assert ids == ["MLB1", "MLB2", "MLB3"]
    assert [request.url.params["offset"] for request in captured] == ["0", "2"]
    assert source.api_calls == 2
    await client.aclose()


async def test_multiget_batches_and_warns_about_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(source_module, "ITEMS_MULTIGET", 2)
    client, captured = _pair(
        [
            httpx.Response(
                200,
                json=[
                    {"code": 200, "body": {"id": "MLB1"}},
                    {"code": 404, "body": {"message": "not found"}},
                ],
            ),
            httpx.Response(200, json=[{"code": 200, "body": {"id": "MLB3"}}]),
        ]
    )
    source = MlSource(client, "2617251613", spacing=0)

    items = await source.items(["MLB1", "MLB2", "MLB3"])

    assert [item["id"] for item in items] == ["MLB1", "MLB3"]
    assert len(captured) == 2
    assert captured[0].url.params["ids"] == "MLB1,MLB2"
    assert any("404" in warning for warning in source.warnings)
    await client.aclose()


async def test_visits_never_exceed_the_provider_limit() -> None:
    client, captured = _pair([httpx.Response(200, json={"total_visits": 0, "results": []})])
    source = MlSource(client, "2617251613", spacing=0)

    await source.visits("MLB1", days=365)

    assert captured[0].url.params["last"] == "150"
    await client.aclose()


async def test_orders_paginate_and_send_update_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(source_module, "ORDERS_PAGE", 2)
    client, captured = _pair(
        [
            httpx.Response(200, json={"paging": {"total": 3}, "results": [{"id": 1}, {"id": 2}]}),
            httpx.Response(200, json={"paging": {"total": 3}, "results": [{"id": 3}]}),
        ]
    )
    source = MlSource(client, "2617251613", spacing=0)

    orders = await source.orders(datetime(2026, 10, 1, 12, tzinfo=UTC))

    assert [order["id"] for order in orders] == [1, 2, 3]
    assert captured[0].url.params["order.date_last_updated.from"] == (
        "2026-10-01T09:00:00.000-03:00"
    )
    assert captured[1].url.params["offset"] == "2"
    await client.aclose()


async def test_missing_shipment_is_a_warning_not_a_crash() -> None:
    client, _ = _pair([httpx.Response(404, json={"message": "not found"})])
    source = MlSource(client, "2617251613", spacing=0)

    assert await source.shipment(123) is None
    assert any("404" in warning for warning in source.warnings)
    await client.aclose()
