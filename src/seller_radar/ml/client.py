"""Cliente HTTP da API do Mercado Livre — somente leitura.

Invariante de segurança (D6 em docs/arquitetura.md): nenhuma requisição que não
seja `GET` sai desta classe. A checagem acontece antes do transporte, não depois
da resposta, e há teste automatizado que falha se a regra mudar.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx

API_BASE_URL = "https://api.mercadolibre.com"


class ReadOnlyViolation(RuntimeError):
    """Levantada quando algo tenta escrever na conta do Mercado Livre."""


class MercadoLivreError(RuntimeError):
    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"HTTP {status_code}: {body[:300]}")
        self.status_code = status_code
        self.body = body


class MercadoLivreClient:
    """Acesso à API com `GET` obrigatório e backoff em 429/5xx."""

    def __init__(
        self,
        access_token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 20.0,
        max_attempts: int = 3,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=API_BASE_URL,
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "User-Agent": "seller-radar/0.1 (+somente leitura)",
            },
        )
        self._max_attempts = max_attempts

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> MercadoLivreClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    @staticmethod
    def _guard(method: str) -> str:
        normalized = method.upper()
        if normalized != "GET":
            raise ReadOnlyViolation(
                f"{normalized} bloqueado: este cliente só permite GET na API do Mercado Livre"
            )
        return normalized

    async def get_json(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = await self.get(path, params=params)
        if not isinstance(payload, dict):
            raise MercadoLivreError(0, f"resposta de {path} não é um objeto JSON")
        return payload

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        """GET cru, para endpoints que devolvem lista (multiget e tarifas)."""
        return await self._request("GET", path, params=params)

    async def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None
    ) -> Any:
        method = self._guard(method)
        last_error: Exception | None = None

        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._client.request(method, path, params=params)
            except httpx.TransportError as exc:
                last_error = exc
            else:
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt == self._max_attempts:
                        raise MercadoLivreError(response.status_code, response.text)
                    await asyncio.sleep(self._backoff(attempt, response))
                    continue
                if response.status_code >= 400:
                    # 4xx definitivo: repetir não resolve.
                    raise MercadoLivreError(response.status_code, response.text)
                return response.json()

            await asyncio.sleep(self._backoff(attempt, None))

        raise MercadoLivreError(0, f"falha de transporte: {last_error}")

    @staticmethod
    def _backoff(attempt: int, response: httpx.Response | None) -> float:
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                return min(float(retry_after), 60.0)
        return min(0.5 * 2 ** (attempt - 1), 8.0) + random.uniform(0, 0.25)
