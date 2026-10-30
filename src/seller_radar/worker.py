"""Worker — sincronização diária do Mercado Livre.

Separado do `web` por decisão D2: falha no worker não derruba o painel. O
agendador é o APScheduler (D4), com cron no fuso de `TIMEZONE`; o lock de
execução é um advisory lock do Postgres, não um serviço extra (D5).

Encerramento: SIGTERM/SIGINT param o agendador. Se uma execução estiver em
andamento, a transação corrente é desfeita pelo Postgres e a linha `running`
órfã é marcada como `failed` no próximo início (`reap_stale_runs`).

Uso: python -m seller_radar.worker
"""

from __future__ import annotations

import logging
import signal
from datetime import datetime
from threading import Event
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from seller_radar.config import get_settings
from seller_radar.crypto import TokenCipher
from seller_radar.db import SessionLocal
from seller_radar.models import MlCredential, User
from seller_radar.sync.engine import reap_stale_runs
from seller_radar.sync.runner import run_sync

logger = logging.getLogger("seller_radar.worker")


def parse_run_time(value: str) -> tuple[int, int]:
    """`HH:MM` → (hora, minuto), falhando cedo em valor inválido."""
    try:
        hour_text, minute_text = value.strip().split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"DAILY_RUN_TIME inválido: {value!r} (esperado HH:MM)") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"DAILY_RUN_TIME fora de faixa: {value!r}")
    return hour, minute


def run_daily_job() -> None:
    """Executa a sincronização de todas as contas conectadas.

    Uma conta com problema não impede as outras: cada uma tem lock e `run`
    próprios, e a falha fica registrada no banco antes de seguir.
    """
    settings = get_settings()
    if not settings.sync_enabled:
        logger.info("sincronização desativada por SYNC_ENABLED=false")
        return

    cipher = TokenCipher(settings.token_encryption_key)
    with SessionLocal() as db:
        user_ids = list(
            db.scalars(
                select(User.id)
                .join(MlCredential, MlCredential.user_id == User.id)
                .order_by(User.id)
            )
        )
        if not user_ids:
            logger.info("nenhuma conta do Mercado Livre conectada; nada a sincronizar")
            return

        for user_id in user_ids:
            try:
                outcome = run_sync(db, settings, cipher, user_id=user_id)
            except Exception:
                logger.exception("sincronização do usuário %s falhou", user_id)
                continue
            logger.info(
                "sincronização %s: status=%s anúncios=%d pedidos=%d dias_de_visita=%d "
                "chamadas=%d avisos=%d",
                user_id,
                outcome.status,
                outcome.items,
                outcome.orders,
                outcome.visits,
                outcome.api_calls,
                len(outcome.warnings),
            )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    settings = get_settings()
    hour, minute = parse_run_time(settings.daily_run_time)
    tz = ZoneInfo(settings.timezone)

    stop = Event()
    scheduler = BlockingScheduler(timezone=tz)
    scheduler.add_job(
        run_daily_job,
        CronTrigger(hour=hour, minute=minute, timezone=tz),
        id="daily-sync",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        # Host de 1 vCPU pode acordar atrasado: 1 h de tolerância, sem rajada.
        misfire_grace_time=3600,
    )

    def _handle(signum: int, _frame: object) -> None:
        logger.info("sinal %s recebido, encerrando", signum)
        stop.set()
        scheduler.shutdown(wait=False)

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    with SessionLocal() as db:
        reaped = reap_stale_runs(db)
    if reaped:
        logger.warning("%d execução(ões) órfã(s) marcada(s) como falha", reaped)

    next_run: datetime | None = None
    job = scheduler.get_job("daily-sync")
    if job is not None:
        next_run = job.trigger.get_next_fire_time(None, datetime.now(tz))
    logger.info(
        "worker iniciado; sincronização diária às %02d:%02d (%s), próxima em %s",
        hour,
        minute,
        settings.timezone,
        next_run.strftime("%d/%m/%Y %H:%M") if next_run else "?",
    )
    try:
        scheduler.start()
    finally:
        if not stop.is_set():
            scheduler.shutdown(wait=False)
        logger.info("worker encerrado")


if __name__ == "__main__":
    main()
