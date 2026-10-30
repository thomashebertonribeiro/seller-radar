"""Ponto de entrada da sincronização: lock, token e cliente.

Duas garantias que só existem aqui:

1. **Nunca duas execuções ao mesmo tempo.** O lock é um advisory lock do
   Postgres mantido em uma conexão dedicada durante toda a execução. Se outra
   execução (agendada ou manual) estiver rodando, esta é registrada como
   `skipped` e termina sem erro — sem fila, sem Redis.
2. **O token é renovado antes de sair para a rede**, pela mesma função usada no
   painel, sob o lock de credencial da Fase 2.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from seller_radar.config import Settings
from seller_radar.crypto import TokenCipher
from seller_radar.db import engine
from seller_radar.ml.client import MercadoLivreClient
from seller_radar.ml.credentials import CredentialMissing, get_access_token
from seller_radar.models import MlCredential, SyncRun, utcnow
from seller_radar.sync.engine import SyncOptions, SyncOutcome, run_sync_async
from seller_radar.sync.source import MlSource

# Namespace separado do lock de renovação de token (7300).
LOCK_NAMESPACE = 7301


@contextmanager
def run_lock(user_id: int) -> Iterator[bool]:
    """Tenta o advisory lock em conexão própria e solta no fim."""
    with engine.connect() as conn:
        acquired = bool(
            conn.execute(
                text("SELECT pg_try_advisory_lock(:ns, :key)"),
                {"ns": LOCK_NAMESPACE, "key": user_id},
            ).scalar_one()
        )
        try:
            yield acquired
        finally:
            if acquired:
                conn.execute(
                    text("SELECT pg_advisory_unlock(:ns, :key)"),
                    {"ns": LOCK_NAMESPACE, "key": user_id},
                )
                conn.commit()


def run_sync(
    db: Session,
    settings: Settings,
    cipher: TokenCipher,
    *,
    user_id: int,
    kind: str = "daily",
    mode: str = "incremental",
    now: datetime | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SyncOutcome:
    """Executa uma sincronização completa para um usuário conectado."""
    options = SyncOptions(
        initial_days=settings.sync_initial_days,
        visit_window_days=settings.sync_visit_window_days,
        order_overlap=timedelta(minutes=settings.sync_overlap_minutes),
    )

    with run_lock(user_id) as acquired:
        if not acquired:
            skipped = SyncRun(
                kind=kind,
                mode=mode,
                status="skipped",
                user_id=user_id,
                finished_at=utcnow(),
                error="já existe uma sincronização em andamento",
            )
            db.add(skipped)
            db.commit()
            return SyncOutcome(run_id=skipped.id, status="skipped")

        credential = db.scalar(select(MlCredential).where(MlCredential.user_id == user_id))
        if credential is None:
            raise CredentialMissing("conta do Mercado Livre não conectada")
        if not credential.ml_user_id:
            raise CredentialMissing("credencial sem identificador do vendedor")

        token = get_access_token(db, user_id, settings, cipher)
        seller_id = credential.ml_user_id

        async def _run() -> SyncOutcome:
            async with MercadoLivreClient(token, transport=transport) as client:
                source = MlSource(
                    client,
                    seller_id,
                    spacing=settings.sync_call_spacing_seconds,
                )
                return await run_sync_async(
                    db,
                    source,
                    user_id=user_id,
                    options=options,
                    kind=kind,
                    mode=mode,
                    now=now,
                )

        return asyncio.run(_run())
