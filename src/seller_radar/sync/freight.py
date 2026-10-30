"""Rateio do frete pago pelo vendedor entre as linhas do pedido.

Um envio pertence a um pedido (medido: `/shipments/{id}` traz `order_id`), mas
pode cobrir várias linhas. O custo é rateado proporcionalmente ao valor da linha
e o resultado é marcado como **estimado** sempre que houver mais de uma linha —
o número fecha no total, mas a divisão exata entre itens é desconhecida.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from seller_radar.finance.calculations import allocate
from seller_radar.models import OrderItem, Shipment, utcnow

ZERO = Decimal("0")


def allocate_for_shipment(db: Session, shipment_id: int, *, now: datetime | None = None) -> int:
    """Distribui `seller_cost` pelas linhas do pedido do envio.

    Devolve quantas linhas foram atualizadas. Não faz commit — o chamador decide
    a fronteira transacional.
    """
    now = now or utcnow()
    shipment = db.get(Shipment, shipment_id)
    if shipment is None or shipment.order_id is None:
        return 0

    lines = list(
        db.scalars(
            select(OrderItem)
            .where(OrderItem.order_id == shipment.order_id)
            .order_by(OrderItem.position)
        )
    )
    if not lines:
        return 0

    total = shipment.seller_cost
    if total is None:
        # Custo desconhecido não vira zero: a linha fica sem rateio e a tela
        # mostra "frete não informado".
        return 0

    if total == ZERO:
        parts = [ZERO for _ in lines]
        estimated = False
    elif len(lines) == 1:
        parts = [total]
        estimated = False
    else:
        weights = [line.unit_price * line.quantity for line in lines]
        if sum(weights, ZERO) <= ZERO:
            # Frete pago em pedido de itens sem preço (brinde/erro de cadastro):
            # dividir por igual é a única leitura que fecha no total.
            weights = [Decimal("1") for _ in lines]
        parts, _ = allocate(total, weights)
        estimated = True

    for line, part in zip(lines, parts, strict=True):
        line.freight_allocated = part
        line.freight_estimated = estimated

    shipment.allocated_at = now
    shipment.allocated_lines = len(lines)
    return len(lines)
