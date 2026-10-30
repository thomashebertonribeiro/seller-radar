"""Sincronização contra Postgres real, com fonte falsa.

Cobre o que só o banco prova: upsert idempotente, rateio que fecha no total,
janela de visitas autoritativa, isolamento de registro malformado, execução
falha marcada como falha e lock impedindo duas execuções.

    docker compose --profile test run --rm test pytest -q -m integration
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from seller_radar.db import SessionLocal
from seller_radar.ml.client import MercadoLivreError
from seller_radar.models import (
    Item,
    ItemVisit,
    ItemVisitCoverage,
    Order,
    OrderItem,
    Payment,
    Shipment,
    SyncRun,
    User,
    utcnow,
)
from seller_radar.security import hash_password
from seller_radar.sync.engine import reap_stale_runs, run_sync_async
from seller_radar.sync.runner import run_lock

pytestmark = pytest.mark.integration

SELLER = "2617251613"
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


# Valor real devolvido pela API (38 caracteres): estourou `varchar(24)` no
# primeiro sync em produção e derrubou o `INSERT INTO items` inteiro.
LONG_DOMAIN = "MLB-MANUAL_GARLIC_PRESSES_AND_CHOPPERS"


def _item(item_id: str, *, status: str = "active", price: str = "47.17") -> dict:
    return {
        "id": item_id,
        "seller_id": int(SELLER),
        "title": f"Anúncio {item_id}",
        "status": status,
        "price": price,
        "available_quantity": 10,
        "sold_quantity": 2,
        "domain_id": LONG_DOMAIN,
        "listing_type_id": "gold_special",
        "condition": "new",
        "catalog_listing": False,
        "seller_custom_field": f"sku-{item_id}",
        "date_created": "2026-05-01T10:00:00.000-03:00",
        "last_updated": "2026-10-28T18:30:00.000Z",
    }


def _line(item_id: str, *, quantity: int, price: str, fee: str = "4.58") -> dict:
    return {
        "quantity": quantity,
        "unit_price": price,
        "sale_fee": fee,
        "listing_type_id": "gold_special",
        "currency_id": "BRL",
        "item": {"id": item_id, "title": f"Anúncio {item_id}", "seller_sku": f"sku-{item_id}"},
    }


def _order(order_id: int, lines: list[dict], *, shipping_id: int | None, total: str) -> dict:
    return {
        "id": order_id,
        "seller_id": int(SELLER),
        "status": "paid",
        "date_created": "2026-10-20T09:10:00.000-03:00",
        "date_closed": "2026-10-20T09:11:00.000-03:00",
        "date_last_updated": "2026-10-20T09:12:00.000-03:00",
        "total_amount": total,
        "paid_amount": total,
        "currency_id": "BRL",
        "buyer": {"id": 123456789, "nickname": "comprador"},
        "shipping": {"id": shipping_id} if shipping_id else {},
        "tags": ["paid"],
        "order_items": lines,
        "payments": [
            {
                "id": order_id + 1,
                "status": "approved",
                "payment_type": "bank_transfer",
                "payment_method_id": "pix",
                "installments": 1,
                "total_paid_amount": total,
                "transaction_amount": total,
                "marketplace_fee": None,
                "currency_id": "BRL",
            }
        ],
    }


def _shipment(shipment_id: int, order_id: int) -> dict:
    return {
        "id": shipment_id,
        "order_id": order_id,
        "status": "shipped",
        "mode": "me2",
        "logistic_type": "xd_drop_off",
        "base_cost": 11.79,
        "receiver_address": {"street_name": "Rua Sigilosa", "zip_code": "00000-000"},
        "receiver_id": 123456789,
        "customer_id": 987654321,
    }


def _costs(cost: str) -> dict:
    return {
        "gross_amount": "29.69",
        "senders": [{"user_id": int(SELLER), "cost": cost, "save": "3.54", "discounts": []}],
    }


def _visits(*days: str, total: int | None = None) -> dict:
    return {
        "date_from": "2026-09-30T00:00:00Z",
        "date_to": "2026-10-30T00:00:00Z",
        "total_visits": total if total is not None else len(days),
        "results": [{"date": f"{day}T00:00:00Z", "total": 1} for day in days],
    }


class FakeSource:
    seller_id = SELLER

    def __init__(
        self,
        *,
        items: list[dict],
        orders: list[dict],
        shipments: dict[int, dict],
        costs: dict[int, dict],
        visits: dict[str, dict] | None = None,
        fail_on_orders: bool = False,
    ) -> None:
        self._items = items
        self._orders = orders
        self._shipments = shipments
        self._costs = costs
        self._visits = visits or {}
        self._fail_on_orders = fail_on_orders
        self.api_calls = 0
        self.warnings: list[str] = []
        self.since: list[datetime] = []

    async def items(self, ids=None):
        self.api_calls += 1
        return self._items

    async def visits(self, item_id: str, days: int) -> dict:
        self.api_calls += 1
        return self._visits.get(item_id, {"results": [], "total_visits": 0})

    async def orders(self, since: datetime) -> list[dict]:
        self.api_calls += 1
        self.since.append(since)
        if self._fail_on_orders:
            raise MercadoLivreError(500, "boom")
        return self._orders

    async def shipment(self, shipment_id: int):
        self.api_calls += 1
        return self._shipments.get(shipment_id)

    async def shipment_costs(self, shipment_id: int):
        self.api_calls += 1
        return self._costs.get(shipment_id)


@pytest.fixture
def account() -> Iterator[int]:
    with SessionLocal() as db:
        db.execute(select(1))
        user = User(
            email=f"sync-{secrets.token_hex(5)}@local.invalid",
            password_hash=hash_password(secrets.token_urlsafe(20)),
        )
        db.add(user)
        db.commit()
        user_id = user.id
    try:
        yield user_id
    finally:
        _cleanup(user_id)


def _cleanup(user_id: int) -> None:
    with SessionLocal() as db:
        db.execute(delete(Order))  # linhas e pagamentos caem por cascata
        db.execute(delete(Item))  # visitas e coberturas caem por cascata
        db.execute(delete(Shipment))
        db.execute(delete(SyncRun).where(SyncRun.user_id == user_id))
        db.execute(delete(User).where(User.id == user_id))
        db.commit()


def _ids(prefix: str) -> tuple[str, str]:
    token = secrets.token_hex(4)
    return f"MLB{prefix}{token}", f"MLB{prefix}{token}b"


def _build() -> tuple[FakeSource, dict[str, object]]:
    """Dois pedidos: um com duas linhas (frete rateado) e um com uma linha."""
    item_a, item_b = _ids("A")
    order_multi, order_single = (
        2000019000000000 + secrets.randbelow(99999999),
        2000018000000000 + secrets.randbelow(99999999),
    )
    ship_multi, ship_single = (
        48000000000 + secrets.randbelow(99999999),
        47000000000 + secrets.randbelow(99999999),
    )

    orders = [
        _order(
            order_multi,
            [_line(item_a, quantity=2, price="47.17"), _line(item_b, quantity=1, price="20.00")],
            shipping_id=ship_multi,
            total="114.34",
        ),
        _order(
            order_single,
            [_line(item_a, quantity=1, price="57.99", fee="8.12")],
            shipping_id=ship_single,
            total="57.99",
        ),
    ]
    source = FakeSource(
        items=[_item(item_a), _item(item_b, status="closed")],
        orders=orders,
        shipments={
            ship_multi: _shipment(ship_multi, order_multi),
            ship_single: _shipment(ship_single, order_single),
        },
        costs={ship_multi: _costs("20.85"), ship_single: _costs("8.25")},
        visits={item_a: _visits("2026-10-22")},
    )
    keys = {
        "item_a": item_a,
        "item_b": item_b,
        "order_multi": order_multi,
        "order_single": order_single,
        "ship_multi": ship_multi,
        "ship_single": ship_single,
        "orders": orders,
    }
    return source, keys


async def test_full_sync_persists_and_allocates(account: int) -> None:
    source, keys = _build()

    with SessionLocal() as db:
        outcome = await run_sync_async(db, source, user_id=account, now=NOW)

    assert outcome.status == "success"
    assert (outcome.items, outcome.orders, outcome.visits) == (2, 2, 1)
    # 1 items + 1 visits (só o ativo) + 1 orders + 2 shipments + 2 costs
    assert outcome.api_calls == 7

    with SessionLocal() as db:
        items = {row.id: row for row in db.scalars(select(Item))}
        assert items[str(keys["item_a"])].status == "active"
        assert items[str(keys["item_b"])].status == "closed"
        assert items[str(keys["item_a"])].seller_sku == f"sku-{keys['item_a']}"
        # Regressão: o domínio real tem 38 caracteres e é gravado inteiro.
        assert items[str(keys["item_a"])].domain_id == LONG_DOMAIN

        orders = {row.id: row for row in db.scalars(select(Order))}
        assert set(orders) == {keys["order_multi"], keys["order_single"]}
        assert orders[int(keys["order_multi"])].buyer_id == "123456789"

        lines = list(
            db.scalars(
                select(OrderItem)
                .where(OrderItem.order_id == keys["order_multi"])
                .order_by(OrderItem.position)
            )
        )
        assert [line.position for line in lines] == [1, 2]
        # `sale_fee` é por unidade: 4,58 continua 4,58 com quantidade 2.
        assert lines[0].sale_fee_unit == Decimal("4.58")

        assert db.scalar(select(Payment).where(Payment.order_id == keys["order_single"]))
        assert db.scalar(
            select(Shipment).where(Shipment.id == keys["ship_multi"])
        ).seller_cost == Decimal("20.85")

        # Rateio fecha no total do envio e é marcado como estimado.
        allocated = sum(line.freight_allocated for line in lines)
        assert allocated == Decimal("20.85")
        assert all(line.freight_estimated for line in lines)

        single = db.scalar(select(OrderItem).where(OrderItem.order_id == keys["order_single"]))
        assert single.freight_allocated == Decimal("8.25")
        assert single.freight_estimated is False

        visit = db.scalar(select(ItemVisit).where(ItemVisit.item_id == keys["item_a"]))
        assert visit.visits == 1

        coverage = {row.item_id: row for row in db.scalars(select(ItemVisitCoverage))}
        assert coverage[str(keys["item_a"])].authoritative is True
        assert coverage[str(keys["item_b"])].authoritative is False

        run = db.scalar(select(SyncRun).where(SyncRun.id == outcome.run_id))
        assert run is not None
        assert run.status == "success"
        assert run.checkpoint["orders_since"] == NOW.isoformat()
        assert run.items_synced == 2
        assert run.orders_synced == 2


async def test_second_run_is_idempotent_and_incremental(account: int) -> None:
    source, _ = _build()
    with SessionLocal() as db:
        await run_sync_async(db, source, user_id=account, now=NOW)

    with SessionLocal() as db:
        before = sorted(
            (line.order_id, line.position, line.freight_allocated)
            for line in db.scalars(select(OrderItem))
        )

    later = NOW + timedelta(days=1)
    with SessionLocal() as db:
        outcome = await run_sync_async(db, source, user_id=account, now=later)

    assert outcome.status == "success"
    # A segunda execução parte da marca d'água menos a sobreposição (1 h).
    assert source.since[-1] == NOW - timedelta(hours=1)

    with SessionLocal() as db:
        assert len(list(db.scalars(select(Order)))) == 2
        assert len(list(db.scalars(select(OrderItem)))) == 3
        assert len(list(db.scalars(select(Payment)))) == 2
        after = sorted(
            (line.order_id, line.position, line.freight_allocated)
            for line in db.scalars(select(OrderItem))
        )
    assert after == before


async def test_closed_item_does_not_lose_collected_visits(account: int) -> None:
    source, keys = _build()
    with SessionLocal() as db:
        await run_sync_async(db, source, user_id=account, now=NOW)

    source._items = [
        _item(str(keys["item_a"]), status="closed"),
        _item(str(keys["item_b"]), status="closed"),
    ]
    source._visits = {}
    with SessionLocal() as db:
        await run_sync_async(db, source, user_id=account, now=NOW + timedelta(days=2))

    with SessionLocal() as db:
        visits = list(db.scalars(select(ItemVisit)))
        coverage = db.scalar(
            select(ItemVisitCoverage).where(ItemVisitCoverage.item_id == keys["item_a"])
        )
    assert len(visits) == 1, "visita coletada enquanto ativo não pode ser apagada"
    assert coverage.authoritative is False
    assert coverage.points_returned == 0


async def test_malformed_order_is_isolated(account: int) -> None:
    source, keys = _build()
    broken = _order(
        2000017000000000 + secrets.randbelow(99999999),
        [_line(str(keys["item_a"]), quantity=0, price="10.00")],
        shipping_id=None,
        total="10.00",
    )
    source._orders = [*source._orders, broken]

    with SessionLocal() as db:
        outcome = await run_sync_async(db, source, user_id=account, now=NOW)

    assert outcome.status == "success"
    assert any("pedido ignorado" in warning for warning in outcome.warnings)
    with SessionLocal() as db:
        assert len(list(db.scalars(select(Order)))) == 2


async def test_transport_failure_marks_run_failed_but_keeps_catalog(account: int) -> None:
    source, _ = _build()
    source._fail_on_orders = True

    with SessionLocal() as db, pytest.raises(MercadoLivreError):
        await run_sync_async(db, source, user_id=account, now=NOW)

    with SessionLocal() as db:
        run = db.scalar(select(SyncRun).order_by(SyncRun.started_at.desc()))
        assert run.status == "failed"
        assert "boom" in run.error
        assert run.finished_at is not None
        assert len(list(db.scalars(select(Item)))) == 2, "o catálogo já gravado é preservado"


def test_lock_prevents_two_runs_for_the_same_account() -> None:
    with run_lock(123456789) as first:
        assert first is True
        with run_lock(123456789) as second:
            assert second is False
        with run_lock(987654321) as other_account:
            assert other_account is True
    with run_lock(123456789) as again:
        assert again is True


def test_reaper_marks_orphan_runs(account: int) -> None:
    with SessionLocal() as db:
        db.add(
            SyncRun(
                kind="daily",
                status="running",
                user_id=account,
                started_at=utcnow() - timedelta(hours=3),
            )
        )
        db.commit()
        assert reap_stale_runs(db) == 1
        run = db.scalar(select(SyncRun).where(SyncRun.user_id == account))
        assert run.status == "failed"
        assert "interrompida" in run.error
