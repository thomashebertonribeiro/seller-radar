"""Leitura paginada da API do Mercado Livre.

Esta camada só busca e devolve payload; não grava nada. Isolá-la permite testar
a orquestração com uma fonte falsa, sem internet, e manter um único lugar onde
os limites medidos da API vivem:

- `items/search` aceita `limit=100` (medido; 56 anúncios em uma página).
- multiget `/items?ids=` aceita lotes; usamos 20 por chamada.
- `visits/time_window` aceita no máximo **150 dias** (acima disso, HTTP 400).
- `orders/search` aceita `order.date_last_updated.from`; paginamos por `offset`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from seller_radar.ml.client import MercadoLivreClient, MercadoLivreError

ITEMS_PAGE = 100
ITEMS_MULTIGET = 20
ORDERS_PAGE = 50
MAX_PAGES = 200
SITE_TZ = ZoneInfo("America/Sao_Paulo")


def _chunks(values: Sequence[str], size: int) -> list[Sequence[str]]:
    return [values[i : i + size] for i in range(0, len(values), size)]


def api_datetime(value: datetime) -> str:
    """Formato que o Mercado Livre aceita nos filtros de data (medido: -03:00)."""
    return value.astimezone(SITE_TZ).isoformat(timespec="milliseconds")


class MlSource:
    """Fonte de dados real, com espaçamento entre chamadas e contagem de uso."""

    def __init__(
        self,
        client: MercadoLivreClient,
        seller_id: str,
        *,
        spacing: float = 0.2,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.client = client
        self.seller_id = seller_id
        self.api_calls = 0
        self.warnings: list[str] = []
        self._spacing = max(spacing, 0.0)
        self._sleep = sleep

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if self.api_calls and self._spacing:
            await self._sleep(self._spacing)
        self.api_calls += 1
        return await self.client.get(path, params=params)

    async def item_ids(self) -> list[str]:
        ids: list[str] = []
        offset = 0
        for _ in range(MAX_PAGES):
            payload = await self._get(
                f"/users/{self.seller_id}/items/search",
                {"limit": ITEMS_PAGE, "offset": offset},
            )
            results = payload.get("results") or []
            if not results:
                break
            ids.extend(str(item_id) for item_id in results)
            offset += len(results)
            total = int((payload.get("paging") or {}).get("total") or 0)
            if offset >= total:
                break
        else:
            self.warnings.append("items/search interrompido no limite de páginas")
        return ids

    async def items(self, ids: Sequence[str] | None = None) -> list[dict[str, Any]]:
        """Multiget em lotes. Entradas com erro são registradas e ignoradas."""
        ids = list(ids) if ids is not None else await self.item_ids()
        found: list[dict[str, Any]] = []
        for chunk in _chunks(ids, ITEMS_MULTIGET):
            payload = await self._get("/items", {"ids": ",".join(chunk)})
            if not isinstance(payload, list):
                raise MercadoLivreError(0, "multiget /items não devolveu lista")
            for entry in payload:
                if not isinstance(entry, dict):
                    continue
                code = entry.get("code")
                body = entry.get("body")
                if code == 200 and isinstance(body, dict):
                    found.append(body)
                else:
                    named = body.get("id") if isinstance(body, dict) else None
                    self.warnings.append(f"anúncio {named or '?'}: multiget devolveu HTTP {code}")
        return found

    async def visits(self, item_id: str, days: int) -> dict[str, Any]:
        # O teto de 150 dias é do provedor; pedir mais devolve 400.
        capped = max(1, min(days, 150))
        return await self._get(
            f"/items/{item_id}/visits/time_window", {"last": capped, "unit": "day"}
        )

    async def orders(self, since: datetime) -> list[dict[str, Any]]:
        orders: list[dict[str, Any]] = []
        offset = 0
        params: dict[str, Any] = {
            "seller": self.seller_id,
            "limit": ORDERS_PAGE,
            "sort": "date_desc",
            "order.date_last_updated.from": api_datetime(since),
        }
        for _ in range(MAX_PAGES):
            payload = await self._get("/orders/search", {**params, "offset": offset})
            results = payload.get("results") or []
            if not results:
                break
            orders.extend(r for r in results if isinstance(r, dict))
            offset += len(results)
            total = int((payload.get("paging") or {}).get("total") or 0)
            if offset >= total:
                break
        else:
            self.warnings.append("orders/search interrompido no limite de páginas")
        return orders

    async def shipment(self, shipment_id: int) -> dict[str, Any] | None:
        return await self._optional(f"/shipments/{shipment_id}", f"envio {shipment_id}")

    async def shipment_costs(self, shipment_id: int) -> dict[str, Any] | None:
        return await self._optional(
            f"/shipments/{shipment_id}/costs", f"custos do envio {shipment_id}"
        )

    async def _optional(self, path: str, label: str) -> dict[str, Any] | None:
        try:
            payload = await self._get(path)
        except MercadoLivreError as exc:
            if exc.status_code == 404:
                self.warnings.append(f"{label}: não encontrado (HTTP 404)")
                return None
            raise
        return payload if isinstance(payload, dict) else None
