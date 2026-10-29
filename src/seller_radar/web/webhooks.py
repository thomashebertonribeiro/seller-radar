"""Recepção de notificações (webhooks) do Mercado Livre.

Por que existe já na Fase 2, se a sincronização ainda não usa notificações: o
DevCenter **exige** uma URL de retorno de chamada para salvar o aplicativo.

O contrato do Mercado Livre é rígido e não admite "depois eu implemento":

- `POST` na URL, com JSON;
- resposta **HTTP 200 em até 500 ms**;
- sem 200, há novas tentativas por 1 hora e, em seguida, o tópico é
  **desativado por fallback** na aplicação.

Por isso esta rota responde 200 sempre e não faz trabalho nenhum no caminho da
requisição. Nada é gravado, nada é consultado na API. Quando a sincronização
existir (Fase 3), o corpo será enfileirado — fora do ciclo de resposta, para não
ameaçar os 500 ms.

A rota é pública por natureza: o Mercado Livre não envia sessão nem CSRF. Um
`POST` forjado aqui não muda estado; no máximo gera uma linha de log.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from seller_radar.config import Settings, get_settings

logger = logging.getLogger("seller_radar.webhooks")

router = APIRouter(tags=["webhooks"])

# Tópicos que interessam ao Seller Radar. Hoje servem só para o log ficar útil;
# na Fase 3 passam a disparar sincronização.
INTERESTING_TOPICS = frozenset({"orders_v2", "items", "shipments", "payments"})


@router.post("/webhooks/mercadolivre")
async def mercadolivre_notification(
    request: Request, settings: Settings = Depends(get_settings)
) -> JSONResponse:
    try:
        payload: dict[str, Any] = await request.json()
    except Exception:
        # Corpo ilegível: ainda 200, para não alimentar retentativas do provedor.
        logger.warning("notificação do Mercado Livre com corpo ilegível")
        return JSONResponse({"status": "ok"})

    application_id = payload.get("application_id")
    owned = not settings.ml_client_id or str(application_id) == str(settings.ml_client_id)

    topic = payload.get("topic")
    if owned and topic in INTERESTING_TOPICS:
        logger.info(
            "notificação ML: topic=%s resource=%s user_id=%s",
            topic,
            payload.get("resource"),
            payload.get("user_id"),
        )
    else:
        logger.debug("notificação ML ignorada: topic=%s application_id=%s", topic, application_id)

    return JSONResponse({"status": "ok"})
