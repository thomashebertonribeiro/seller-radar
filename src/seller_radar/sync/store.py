"""Gravação idempotente: `INSERT ... ON CONFLICT DO UPDATE`.

Toda escrita é repetível: rodar a mesma sincronização duas vezes não duplica
nada e não muda nenhum valor. É isso que permite retomar uma execução falha sem
apagar o que já foi coletado.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from seller_radar.models import (
    Item,
    ItemVisit,
    ItemVisitCoverage,
    Order,
    OrderItem,
    Payment,
    Shipment,
)

# Colunas geradas pelo banco ou imutáveis: não entram no `SET` do upsert.
_KEEP = frozenset({"first_seen_at", "created_at"})


def _upsert(
    db: Session,
    model: Any,
    rows: Sequence[dict[str, Any]],
    *,
    conflict: Sequence[str],
    columns_update: Sequence[str] | None = None,
) -> None:
    if not rows:
        return
    stmt = pg_insert(model).values(list(rows))
    columns = (
        columns_update if columns_update is not None else [c for c in rows[0] if c not in _KEEP]
    )
    columns = [c for c in columns if c not in _KEEP]
    if columns:
        stmt = stmt.on_conflict_do_update(
            index_elements=list(conflict),
            set_={c: getattr(stmt.excluded, c) for c in columns},
        )
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=list(conflict))
    db.execute(stmt)


def upsert_items(db: Session, rows: Sequence[dict[str, Any]]) -> int:
    _upsert(db, Item, rows, conflict=["id"])
    return len(rows)


def upsert_order(
    db: Session,
    order: dict[str, Any],
    items: Sequence[dict[str, Any]],
    payments: Sequence[dict[str, Any]],
) -> None:
    """Grava o pedido e **substitui** suas linhas e pagamentos.

    Substituir em vez de casar por chave é proposital: se o Mercado Livre
    remover uma linha (cancelamento parcial), um upsert deixaria a linha antiga
    viva e a receita ficaria maior que a real.
    """
    _upsert(db, Order, [order], conflict=["id"])
    db.execute(delete(OrderItem).where(OrderItem.order_id == order["id"]))
    db.execute(delete(Payment).where(Payment.order_id == order["id"]))
    if items:
        db.execute(pg_insert(OrderItem).values(list(items)))
    if payments:
        db.execute(pg_insert(Payment).values(list(payments)))


def upsert_shipment(db: Session, row: dict[str, Any]) -> None:
    """Dados descritivos do envio. Custos ficam em `apply_shipment_costs`."""
    _upsert(
        db,
        Shipment,
        [row],
        conflict=["id"],
        columns_update=[
            "order_id",
            "status",
            "shipping_mode",
            "logistic_type",
            "base_cost",
            "list_cost",
            "currency_id",
            "cost_components",
            "last_synced_at",
        ],
    )


def apply_shipment_costs(
    db: Session, shipment_id: int, values: dict[str, Any], *, now: datetime
) -> None:
    db.execute(
        update(Shipment).where(Shipment.id == shipment_id).values(**values, costs_collected_at=now)
    )


def replace_visits(
    db: Session,
    item_id: str,
    rows: Sequence[tuple[date, int]],
    coverage: dict[str, Any],
    *,
    now: datetime,
) -> int:
    """Grava as visitas da janela e o registro de cobertura.

    Quando a cobertura é autoritativa (anúncio ativo), a janela manda: linhas
    antigas dentro dela são apagadas e substituídas. Sem isso, uma correção do
    provedor para baixo deixaria o número antigo para sempre.
    """
    if coverage.get("authoritative"):
        start, end = coverage.get("covered_from"), coverage.get("covered_to")
        if start is not None and end is not None:
            db.execute(
                delete(ItemVisit).where(
                    ItemVisit.item_id == item_id,
                    ItemVisit.visit_date >= start,
                    ItemVisit.visit_date <= end,
                )
            )

    if rows:
        _upsert(
            db,
            ItemVisit,
            [
                {"item_id": item_id, "visit_date": day, "visits": total, "collected_at": now}
                for day, total in rows
            ],
            conflict=["item_id", "visit_date"],
        )

    record_coverage(db, item_id, coverage)
    return len(rows)


def record_coverage(db: Session, item_id: str, coverage: dict[str, Any]) -> None:
    _upsert(
        db,
        ItemVisitCoverage,
        [{"item_id": item_id, **coverage}],
        conflict=["item_id", "window_days"],
    )
