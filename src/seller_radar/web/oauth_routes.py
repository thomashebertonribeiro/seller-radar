"""Rotas de conexão com o Mercado Livre (Fase 2).

Fluxo:

    POST /auth/mercadolivre/start      → cria `state` + PKCE, manda ao ML
    GET  /auth/mercadolivre/callback   → consome `state`, troca `code` por tokens
    POST /auth/mercadolivre/disconnect → apaga a credencial

O `start` é `POST` com CSRF, não um link: sem isso, um terceiro poderia induzir
a sessão da vítima a iniciar uma autorização. O `state` também é amarrado ao
`user_id` da sessão, então um callback capturado não serve para outra conta.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import delete
from sqlalchemy.orm import Session

from seller_radar.config import Settings, get_settings
from seller_radar.crypto import TokenCipher
from seller_radar.db import get_db
from seller_radar.ml.client import MercadoLivreClient
from seller_radar.ml.credentials import (
    begin_authorization,
    consume_state,
    store_tokens,
)
from seller_radar.ml.oauth import OAuthError, build_authorize_url, code_challenge, exchange_code
from seller_radar.models import MlCredential
from seller_radar.security import CSRF_SESSION_KEY, csrf_matches

logger = logging.getLogger("seller_radar.oauth")

router = APIRouter(prefix="/auth/mercadolivre", tags=["mercadolivre"])


def _cipher(settings: Settings) -> TokenCipher:
    return TokenCipher(settings.token_encryption_key)


def _back(code: str) -> RedirectResponse:
    """Volta ao painel com um código curto; o texto fica no template."""
    return RedirectResponse(f"/?ml={code}", status_code=303)


def _logged_user(request: Request) -> int | None:
    user_id = request.session.get("user_id")
    return int(user_id) if user_id else None


@router.post("/start")
def start(
    request: Request,
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    user_id = _logged_user(request)
    if user_id is None:
        return RedirectResponse("/login", status_code=303)
    if not csrf_matches(request.session.get(CSRF_SESSION_KEY), csrf_token):
        return _back("csrf")
    if not settings.ml_configured:
        return _back("nao_configurado")

    pending = begin_authorization(db, user_id, _cipher(settings))
    url = build_authorize_url(
        settings, pending.state, pkce_challenge=code_challenge(pending.code_verifier)
    )
    return RedirectResponse(url, status_code=303)


@router.get("/callback")
def callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    user_id = _logged_user(request)
    if user_id is None:
        return RedirectResponse("/login", status_code=303)

    if error:
        # O vendedor recusou a autorização no site do Mercado Livre.
        logger.info("autorização recusada no provedor: %s", error)
        return _back("negado")

    if not code or not state:
        return _back("invalido")

    verifier = consume_state(db, state, user_id, _cipher(settings))
    if verifier is None:
        # Desconhecido, de outro usuário, já usado ou expirado.
        logger.warning("state inválido ou reutilizado no callback")
        return _back("state")

    try:
        tokens = asyncio.run(exchange_code(settings, code, code_verifier=verifier))
    except OAuthError as exc:
        logger.warning("falha na troca do código: %s", exc.error)
        return _back("falhou")

    nickname, site_id = _profile(tokens.access_token)
    try:
        store_tokens(
            db, user_id, tokens, _cipher(settings), nickname=nickname, site_id=site_id
        )
    except Exception:
        # O token já foi emitido pelo provedor; sem gravar, a autorização se
        # perde e o vendedor precisa repetir o fluxo. Falhar aqui é erro nosso,
        # então o log é completo e a página mostra uma mensagem, não um 500.
        logger.exception("falha ao gravar a credencial do Mercado Livre")
        db.rollback()
        return _back("falhou")

    logger.info("conta do Mercado Livre conectada: ml_user_id=%s", tokens.ml_user_id)
    return _back("conectado")


@router.post("/disconnect")
def disconnect(
    request: Request,
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
) -> Response:
    user_id = _logged_user(request)
    if user_id is None:
        return RedirectResponse("/login", status_code=303)
    if not csrf_matches(request.session.get(CSRF_SESSION_KEY), csrf_token):
        return _back("csrf")

    db.execute(delete(MlCredential).where(MlCredential.user_id == user_id))
    db.commit()
    logger.info("conta do Mercado Livre desconectada pelo usuário %s", user_id)
    return _back("desconectado")


def _profile(access_token: str) -> tuple[str | None, str | None]:
    """Apelido e site do vendedor.

    Falha aqui não invalida a conexão: o token já foi gravado com sucesso e o
    perfil pode ser buscado de novo depois.
    """

    async def _fetch() -> dict:
        async with MercadoLivreClient(access_token) as client:
            return await client.get_json("/users/me")

    try:
        data = asyncio.run(_fetch())
    except Exception as exc:  # perfil é acessório; conexão já está garantida
        logger.info("não foi possível ler o perfil do vendedor: %s", type(exc).__name__)
        return None, None
    return data.get("nickname"), data.get("site_id")
