"""Tradução de payload da API para linhas do banco — sem rede e sem sessão.

Funções puras de propósito: o que a API manda é o contrato mais arriscado do
sistema, e testar isso não pode exigir Postgres nem internet. Campos que não
existem no payload simplesmente ficam `None` — nenhum valor é inventado.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from seller_radar.sync.values import (
    BadValue,
    as_int,
    day,
    moment,
    money,
    numbers_as_text,
)


class BadPayload(ValueError):
    """Payload sem campo obrigatório. O registro é isolado, a execução continua."""


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_id(payload: dict[str, Any], key: str, *, kind: str) -> str:
    value = _text(payload.get(key))
    if value is None:
        raise BadPayload(f"{kind} sem `{key}`")
    return value


def item_row(payload: dict[str, Any], now: datetime) -> dict[str, Any]:
    """`GET /items/{id}` ou corpo do multiget `/items?ids=`."""
    item_id = _required_id(payload, "id", kind="anúncio")
    if len(item_id) > 20:
        raise BadPayload(f"anúncio com id inesperado: {item_id!r}")

    sub_status = payload.get("sub_status")
    if isinstance(sub_status, str):
        sub_status = [sub_status]

    return {
        "id": item_id,
        "seller_id": _text(payload.get("seller_id")),
        "title": _text(payload.get("title")),
        "status": _text(payload.get("status")),
        "sub_status": [str(s)[:32] for s in sub_status] if isinstance(sub_status, list) else None,
        "category_id": _text(payload.get("category_id")),
        "domain_id": _text(payload.get("domain_id")),
        "listing_type_id": _text(payload.get("listing_type_id")),
        "condition": _text(payload.get("condition")),
        "catalog_listing": payload.get("catalog_listing"),
        "catalog_product_id": _text(payload.get("catalog_product_id")),
        "price": money(payload.get("price"), field="price"),
        "base_price": money(payload.get("base_price"), field="base_price"),
        "original_price": money(payload.get("original_price"), field="original_price"),
        "currency_id": _text(payload.get("currency_id")),
        "available_quantity": as_int(payload.get("available_quantity")),
        "sold_quantity": as_int(payload.get("sold_quantity")),
        "initial_quantity": as_int(payload.get("initial_quantity")),
        # `seller_custom_field` é onde o ML expõe o SKU do anúncio; pode ser nulo.
        "seller_sku": _text(payload.get("seller_custom_field")),
        "permalink": _text(payload.get("permalink")),
        "date_created": moment(payload.get("date_created")),
        "last_updated": moment(payload.get("last_updated")),
        "start_time": moment(payload.get("start_time")),
        "stop_time": moment(payload.get("stop_time")),
        "last_synced_at": now,
    }


def order_row(payload: dict[str, Any], now: datetime) -> dict[str, Any]:
    raw_id = payload.get("id")
    order_id = as_int(raw_id, field="id do pedido")
    if order_id is None:
        raise BadPayload("pedido sem `id`")

    buyer = payload.get("buyer") or {}
    shipping = payload.get("shipping") or {}
    tags = payload.get("tags")

    return {
        "id": order_id,
        "seller_id": _text(payload.get("seller_id")),
        "status": _text(payload.get("status")),
        "date_created": moment(payload.get("date_created")),
        "date_closed": moment(payload.get("date_closed")),
        "date_last_updated": moment(payload.get("date_last_updated")),
        "total_amount": money(payload.get("total_amount"), field="total_amount"),
        "paid_amount": money(payload.get("paid_amount"), field="paid_amount"),
        "currency_id": _text(payload.get("currency_id")),
        "buyer_id": _text(buyer.get("id")),
        "shipping_id": as_int(shipping.get("id"), field="shipping.id"),
        "tags": [str(t)[:48] for t in tags] if isinstance(tags, list) else None,
        "last_synced_at": now,
    }


def order_item_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Linhas do pedido, com posição estável (1-based) como chave.

    `sale_fee` é **por unidade** — medido em 29/10/2026: o mesmo anúncio em
    pedidos de quantidade 1 e 2 traz o mesmo valor. Multiplicar pela quantidade
    é responsabilidade de quem lê, não da coleta.
    """
    order_id = as_int(payload.get("id"), field="id do pedido")
    if order_id is None:
        raise BadPayload("pedido sem `id`")

    rows: list[dict[str, Any]] = []
    for position, raw in enumerate(payload.get("order_items") or [], start=1):
        if not isinstance(raw, dict):
            raise BadPayload(f"pedido {order_id}: linha {position} não é objeto")
        item = raw.get("item") or {}
        quantity = as_int(raw.get("quantity"), field=f"pedido {order_id} quantidade")
        if quantity is None or quantity <= 0:
            # Sem quantidade não há receita: melhor falhar alto do que gravar zero.
            raise BadPayload(f"pedido {order_id}: linha {position} com quantidade {quantity!r}")
        unit_price = money(raw.get("unit_price"), field=f"pedido {order_id} unit_price")
        if unit_price is None:
            raise BadPayload(f"pedido {order_id}: linha {position} sem preço unitário")

        rows.append(
            {
                "order_id": order_id,
                "position": position,
                "item_id": _text(item.get("id")),
                "title": _text(item.get("title")),
                "seller_sku": _text(item.get("seller_sku"))
                or _text(item.get("seller_custom_field")),
                "listing_type_id": _text(raw.get("listing_type_id")),
                "quantity": quantity,
                "unit_price": unit_price,
                "sale_fee_unit": money(raw.get("sale_fee"), field="sale_fee") or Decimal("0"),
                "currency_id": _text(raw.get("currency_id")),
                "freight_allocated": None,
                "freight_estimated": False,
            }
        )
    return rows


def payment_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    order_id = as_int(payload.get("id"), field="id do pedido")
    if order_id is None:
        raise BadPayload("pedido sem `id`")

    rows: list[dict[str, Any]] = []
    for position, raw in enumerate(payload.get("payments") or [], start=1):
        if not isinstance(raw, dict):
            raise BadPayload(f"pedido {order_id}: pagamento {position} não é objeto")
        rows.append(
            {
                "order_id": order_id,
                "position": position,
                "payment_id": as_int(raw.get("id"), field="payment.id"),
                "status": _text(raw.get("status")),
                "payment_type": _text(raw.get("payment_type")),
                "payment_method_id": _text(raw.get("payment_method_id")),
                "installments": max(as_int(raw.get("installments")) or 1, 1),
                "total_paid_amount": money(raw.get("total_paid_amount"), field="total_paid_amount"),
                "transaction_amount": money(
                    raw.get("transaction_amount"), field="transaction_amount"
                ),
                "marketplace_fee": money(raw.get("marketplace_fee"), field="marketplace_fee"),
                "currency_id": _text(raw.get("currency_id")),
            }
        )
    return rows


def shipment_row(payload: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Só campos escolhidos: o payload traz endereço e id do comprador."""
    shipment_id = as_int(payload.get("id"), field="id do envio")
    if shipment_id is None:
        raise BadPayload("envio sem `id`")

    return {
        "id": shipment_id,
        "order_id": as_int(payload.get("order_id"), field="order_id do envio"),
        "status": _text(payload.get("status")),
        # Medido: `shipping_mode` vem nulo e `mode` traz a modalidade.
        "shipping_mode": _text(payload.get("shipping_mode")) or _text(payload.get("mode")),
        "logistic_type": _text(payload.get("logistic_type")),
        "base_cost": money(payload.get("base_cost"), field="base_cost"),
        "list_cost": money(payload.get("list_cost"), field="list_cost"),
        "currency_id": _text(payload.get("currency_id")),
        "cost_components": _components(payload.get("cost_components")),
        "last_synced_at": now,
    }


def _components(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return numbers_as_text(value)


def shipment_costs(payload: dict[str, Any], seller_id: str) -> dict[str, Any]:
    """Extrai quanto **nosso vendedor** paga de frete em `/shipments/{id}/costs`.

    `senders[]` pode listar mais de um remetente. Só as entradas do nosso
    vendedor contam. Se ele não aparece, o custo fica `None` — desconhecido, não
    zero: atribuir zero faria a margem parecer melhor do que é.
    """
    senders = payload.get("senders") or []
    ours = [s for s in senders if isinstance(s, dict) and str(s.get("user_id")) == str(seller_id)]

    seller_cost: Decimal | None
    cost_save: Decimal | None
    if ours:
        seller_cost = sum(
            (money(s.get("cost"), field="cost") or Decimal("0") for s in ours), Decimal("0")
        )
        saves = [money(s.get("save"), field="save") for s in ours]
        cost_save = (
            sum((v for v in saves if v is not None), Decimal("0"))
            if any(v is not None for v in saves)
            else None
        )
    else:
        seller_cost = None
        cost_save = None

    return {
        "seller_cost": seller_cost,
        "cost_save": cost_save,
        "costs_gross_amount": money(payload.get("gross_amount"), field="gross_amount"),
    }


def visit_rows(payload: dict[str, Any]) -> list[tuple[Any, int]]:
    """Pontos diários de `/items/{id}/visits/time_window`."""
    rows: list[tuple[Any, int]] = []
    for raw in payload.get("results") or []:
        if not isinstance(raw, dict):
            continue
        visit_day = day(raw.get("date"), field="visits.date")
        total = as_int(raw.get("total"), field="visits.total")
        if visit_day is None or total is None or total < 0:
            raise BadPayload(f"ponto de visita inválido: {raw!r}")
        rows.append((visit_day, total))
    return rows


def coverage_values(
    payload: dict[str, Any], *, item_status: str | None, window_days: int, now: datetime
) -> dict[str, Any]:
    """Diz se a janela coletada pode ser lida como verdade absoluta.

    Só anúncio ativo tem cobertura autoritativa: para anúncio encerrado a API
    devolve zero pontos mesmo que tenha havido visitas, então o histórico
    anterior é preservado em vez de apagado.
    """
    from_date = day(payload.get("date_from"), field="date_from")
    to_date = day(payload.get("date_to"), field="date_to")
    authoritative = item_status == "active" and from_date is not None and to_date is not None

    return {
        "window_days": window_days,
        "covered_from": from_date,
        "covered_to": to_date,
        "item_status": item_status,
        "authoritative": authoritative,
        "points_returned": len(payload.get("results") or []),
        "total_reported": as_int(payload.get("total_visits"), field="total_visits"),
        "collected_at": now,
    }


__all__ = [
    "BadPayload",
    "BadValue",
    "coverage_values",
    "item_row",
    "order_item_rows",
    "order_row",
    "payment_rows",
    "shipment_costs",
    "shipment_row",
    "visit_rows",
]
