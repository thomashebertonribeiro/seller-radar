"""Orquestração da sincronização — sem rede própria e sem HTML.

A ordem importa: anúncios antes de visitas (a visita precisa do anúncio para
existir), pedidos antes de envios (o envio é descoberto no pedido) e rateio por
último (depende do custo do envio e das linhas já gravadas).

Fronteira de erro: um registro malformado vira aviso e o restante segue; uma
falha de paginação ou de transporte derruba a execução e o `sync_run` fica
`failed` com o motivo. Sucesso nunca é declarado sobre cobertura desconhecida.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from seller_radar.models import Item, SyncRun, utcnow
from seller_radar.sync import freight, parse, store
from seller_radar.sync.values import BadValue

logger = logging.getLogger("seller_radar.sync")

MAX_WARNINGS = 200


class Source(Protocol):
    seller_id: str
    api_calls: int
    warnings: list[str]

    async def items(self, ids: list[str] | None = None) -> list[dict[str, Any]]: ...

    async def visits(self, item_id: str, days: int) -> dict[str, Any]: ...

    async def orders(self, since: datetime) -> list[dict[str, Any]]: ...

    async def shipment(self, shipment_id: int) -> dict[str, Any] | None: ...

    async def shipment_costs(self, shipment_id: int) -> dict[str, Any] | None: ...


@dataclass(frozen=True)
class SyncOptions:
    initial_days: int = 365
    visit_window_days: int = 150
    order_overlap: timedelta = timedelta(hours=1)


@dataclass
class SyncOutcome:
    run_id: uuid.UUID | None
    status: str
    items: int = 0
    orders: int = 0
    visits: int = 0
    api_calls: int = 0
    warnings: list[str] = field(default_factory=list)


def _cap(warnings: list[str]) -> list[str] | None:
    return warnings[:MAX_WARNINGS] or None


def last_checkpoint(db: Session, user_id: int, kind: str) -> dict[str, Any]:
    row = db.scalar(
        select(SyncRun)
        .where(
            SyncRun.user_id == user_id,
            SyncRun.kind == kind,
            SyncRun.status == "success",
        )
        .order_by(SyncRun.started_at.desc())
        .limit(1)
    )
    return dict(row.checkpoint or {}) if row is not None else {}


def orders_since(
    checkpoint: dict[str, Any], *, now: datetime, options: SyncOptions, full: bool
) -> datetime:
    """Início da janela de pedidos, com sobreposição para não perder atualização.

    A marca d'água é o **início** da execução anterior, não a maior data vista:
    um pedido atualizado entre a consulta e o commit ficaria de fora para sempre.
    """
    if not full:
        raw = checkpoint.get("orders_since")
        if isinstance(raw, str):
            try:
                parsed = datetime.fromisoformat(raw)
            except ValueError:
                parsed = None
            if parsed is not None:
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                return parsed - options.order_overlap
    return now - timedelta(days=options.initial_days)


async def run_sync_async(
    db: Session,
    source: Source,
    *,
    user_id: int,
    options: SyncOptions | None = None,
    kind: str = "daily",
    mode: str = "incremental",
    now: datetime | None = None,
) -> SyncOutcome:
    options = options or SyncOptions()
    now = now or utcnow()
    warnings: list[str] = []

    def warn(message: str) -> None:
        if len(warnings) < MAX_WARNINGS:
            warnings.append(message)

    checkpoint = {} if mode == "full" else last_checkpoint(db, user_id, kind)
    run = SyncRun(kind=kind, mode=mode, status="running", user_id=user_id, checkpoint={})
    db.add(run)
    db.commit()
    run_id = run.id

    items_written = orders_written = visits_written = 0
    try:
        # --- anúncios -------------------------------------------------------
        item_payloads = await source.items()
        rows: list[dict[str, Any]] = []
        for payload in item_payloads:
            try:
                rows.append(parse.item_row(payload, now))
            except (parse.BadPayload, BadValue) as exc:
                warn(f"anúncio ignorado: {exc}")
        items_written = store.upsert_items(db, rows)
        db.commit()

        # --- visitas --------------------------------------------------------
        # Anúncio inativo não expõe visitas: registra-se a cobertura sem chamada,
        # para a tela distinguir "sem coleta" de "zero visitas".
        catalog = db.execute(
            select(Item.id, Item.status).where(Item.seller_id == source.seller_id)
        ).all()
        for index, (item_id, status) in enumerate(catalog, start=1):
            try:
                if status == "active":
                    payload = await source.visits(item_id, options.visit_window_days)
                    points = parse.visit_rows(payload)
                else:
                    payload = {}
                    points = []
                coverage = parse.coverage_values(
                    payload,
                    item_status=status,
                    window_days=options.visit_window_days,
                    now=now,
                )
                visits_written += store.replace_visits(db, item_id, points, coverage, now=now)
            except (parse.BadPayload, BadValue) as exc:
                warn(f"visitas de {item_id}: {exc}")
            if index % 10 == 0:
                db.commit()
        db.commit()

        # --- pedidos e envios ----------------------------------------------
        since = orders_since(checkpoint, now=now, options=options, full=mode == "full")
        shipment_ids: set[int] = set()
        for payload in await source.orders(since):
            try:
                order = parse.order_row(payload, now)
                lines = parse.order_item_rows(payload)
                payments = parse.payment_rows(payload)
            except (parse.BadPayload, BadValue) as exc:
                warn(f"pedido ignorado: {exc}")
                continue
            store.upsert_order(db, order, lines, payments)
            orders_written += 1
            if order["shipping_id"] is not None:
                shipment_ids.add(order["shipping_id"])
        db.commit()

        for shipment_id in sorted(shipment_ids):
            try:
                shipment_payload = await source.shipment(shipment_id)
                if shipment_payload is None:
                    continue
                store.upsert_shipment(db, parse.shipment_row(shipment_payload, now))
                costs_payload = await source.shipment_costs(shipment_id)
                if costs_payload is not None:
                    store.apply_shipment_costs(
                        db,
                        shipment_id,
                        parse.shipment_costs(costs_payload, source.seller_id),
                        now=now,
                    )
            except (parse.BadPayload, BadValue) as exc:
                warn(f"envio {shipment_id}: {exc}")
        db.commit()

        # --- rateio do frete ------------------------------------------------
        for shipment_id in sorted(shipment_ids):
            freight.allocate_for_shipment(db, shipment_id, now=now)
        db.commit()

        warnings.extend(source.warnings)
        outcome = SyncOutcome(
            run_id=run_id,
            status="success",
            items=items_written,
            orders=orders_written,
            visits=visits_written,
            api_calls=source.api_calls,
            warnings=warnings[:MAX_WARNINGS],
        )
        run = db.get(SyncRun, run_id)
        assert run is not None
        run.status = "success"
        run.finished_at = utcnow()
        run.items_synced = items_written
        run.orders_synced = orders_written
        run.visits_synced = visits_written
        run.api_calls = source.api_calls
        run.warnings = _cap(warnings)
        run.checkpoint = {
            "orders_since": now.isoformat(),
            "items_total": items_written,
            "visits_window_days": options.visit_window_days,
        }
        db.commit()
        return outcome

    except Exception as exc:
        db.rollback()
        warnings.extend(source.warnings)
        failed = db.get(SyncRun, run_id)
        if failed is not None:
            failed.status = "failed"
            failed.finished_at = utcnow()
            failed.error = f"{type(exc).__name__}: {exc}"[:4000]
            failed.api_calls = source.api_calls
            failed.warnings = _cap(warnings)
            db.commit()
        logger.exception("sincronização falhou (run %s)", run_id)
        raise


def reap_stale_runs(
    db: Session, *, older_than: timedelta = timedelta(hours=2), now: datetime | None = None
) -> int:
    """Marca execuções órfãs de processo morto — senão ficam `running` para sempre."""
    now = now or utcnow()
    result = db.execute(
        update(SyncRun)
        .where(SyncRun.status == "running", SyncRun.started_at < now - older_than)
        .values(
            status="failed",
            finished_at=now,
            error="execução interrompida: o processo terminou antes do fim",
        )
    )
    db.commit()
    return int(result.rowcount or 0)
