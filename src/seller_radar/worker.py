"""Worker — esqueleto.

Separado do `web` por decisão D2 (docs/arquitetura.md): falha no worker não
derruba o painel. A Fase 1 entrega apenas o processo vivo com encerramento
limpo; jobs (sincronização, cálculo, relatório) entram nas fases seguintes.

Uso: python -m seller_radar.worker
"""

from __future__ import annotations

import logging
import signal
from threading import Event

logger = logging.getLogger("seller_radar.worker")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    stop = Event()

    def _handle(signum: int, _frame: object) -> None:
        logger.info("sinal %s recebido, encerrando", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    logger.info("worker iniciado sem jobs registrados (Fase 1)")
    stop.wait()
    logger.info("worker encerrado")


if __name__ == "__main__":
    main()
