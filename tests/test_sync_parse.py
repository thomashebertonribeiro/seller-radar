"""Contratos da API → linhas do banco, sem rede e sem banco.

Os payloads aqui reproduzem o que a conta real devolveu em 29/10/2026 — inclusive
os casos que enganam: `sale_fee` por unidade, frete nulo no pedido,
`marketplace_fee` nulo e visitas ausentes para anúncio encerrado.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from seller_radar.sync import parse
from seller_radar.sync.values import BadValue, money, numbers_as_text, unit_cost

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def test_money_never_returns_float() -> None:
    assert money(19.9) == Decimal("19.90")
    assert isinstance(money(19.9), Decimal)
    assert money(None) is None
    assert money("0.1") == Decimal("0.10")
    with pytest.raises(BadValue):
        money(True)
    with pytest.raises(BadValue):
        money("abc")


def test_unit_cost_keeps_four_decimals() -> None:
    assert unit_cost("12.34567") == Decimal("12.3457")
    assert unit_cost(None) is None


def test_item_row_reads_the_sku_from_seller_custom_field() -> None:
    row = parse.item_row(
        {
            "id": "MLB4313906707",
            "seller_id": 2617251613,
            "title": "Adesivo",
            "status": "active",
            "price": 19.9,
            "available_quantity": 40,
            "sold_quantity": 3,
            "listing_type_id": "gold_special",
            "condition": "new",
            "catalog_listing": False,
            "seller_custom_field": "pt010103",
            "date_created": "2026-05-01T10:00:00.000-03:00",
            "last_updated": "2026-10-28T18:30:00.000Z",
            "sub_status": [],
        },
        NOW,
    )

    assert row["id"] == "MLB4313906707"
    assert row["seller_id"] == "2617251613"
    assert row["seller_sku"] == "pt010103"
    assert row["price"] == Decimal("19.90")
    assert row["last_updated"] == datetime(2026, 10, 28, 18, 30, tzinfo=UTC)
    assert row["last_synced_at"] == NOW


def test_item_row_accepts_missing_sku() -> None:
    row = parse.item_row({"id": "MLB1", "seller_custom_field": None}, NOW)
    assert row["seller_sku"] is None


def test_item_row_keeps_the_whole_domain_slug() -> None:
    """Medido: o domínio real tem 38 caracteres — nada é truncado na coleta."""
    domain = "MLB-MANUAL_GARLIC_PRESSES_AND_CHOPPERS"

    row = parse.item_row({"id": "MLB1", "domain_id": domain}, NOW)

    assert row["domain_id"] == domain
    assert len(row["domain_id"]) == 38


def test_item_row_without_id_is_rejected() -> None:
    with pytest.raises(parse.BadPayload):
        parse.item_row({"title": "sem id"}, NOW)


def test_order_row_keeps_only_the_buyer_id() -> None:
    row = parse.order_row(
        {
            "id": 2000019209106214,
            "seller_id": 2617251613,
            "status": "paid",
            "date_created": "2026-10-20T09:10:00.000-03:00",
            "date_closed": "2026-10-20T09:11:00.000-03:00",
            "total_amount": 57.99,
            "paid_amount": 57.99,
            "currency_id": "BRL",
            "buyer": {"id": 123456789, "nickname": "comprador"},
            "shipping": {"id": 48367189736},
            "tags": ["paid", "delivered"],
        },
        NOW,
    )

    assert row["id"] == 2000019209106214
    assert row["buyer_id"] == "123456789"
    assert row["shipping_id"] == 48367189736
    assert "comprador" not in str(row)
    assert row["tags"] == ["paid", "delivered"]


def test_sale_fee_is_per_unit_and_not_multiplied() -> None:
    """Medido: o mesmo anúncio traz 4,58 tanto em quantidade 1 quanto em 2."""
    payload = {
        "id": 2000019185916504,
        "order_items": [
            {
                "quantity": 2,
                "unit_price": 47.17,
                "sale_fee": 4.58,
                "listing_type_id": "gold_special",
                "currency_id": "BRL",
                "item": {"id": "MLB4313906707", "title": "Adesivo", "seller_sku": "pt010103"},
            }
        ],
        "payments": [],
    }

    lines = parse.order_item_rows(payload)

    assert len(lines) == 1
    assert lines[0]["quantity"] == 2
    assert lines[0]["sale_fee_unit"] == Decimal("4.58")
    assert lines[0]["freight_allocated"] is None
    assert lines[0]["position"] == 1


def test_order_line_with_zero_quantity_fails_loudly() -> None:
    payload = {
        "id": 1,
        "order_items": [{"quantity": 0, "unit_price": 10, "sale_fee": 1, "item": {"id": "MLB1"}}],
    }
    with pytest.raises(parse.BadPayload):
        parse.order_item_rows(payload)


def test_payment_rows_keep_marketplace_fee_but_it_is_not_a_source() -> None:
    rows = parse.payment_rows(
        {
            "id": 2000019209106214,
            "payments": [
                {
                    "id": 186464628310,
                    "status": "approved",
                    "payment_type": "bank_transfer",
                    "payment_method_id": "pix",
                    "installments": 1,
                    "total_paid_amount": 57.99,
                    "transaction_amount": 57.99,
                    "marketplace_fee": None,
                    "currency_id": "BRL",
                }
            ],
        }
    )

    assert rows[0]["payment_id"] == 186464628310
    assert rows[0]["marketplace_fee"] is None
    assert rows[0]["position"] == 1


def test_shipment_costs_uses_only_our_sender() -> None:
    values = parse.shipment_costs(
        {
            "gross_amount": 29.69,
            "senders": [
                {"user_id": 2617251613, "cost": 8.25, "save": 3.54, "discounts": []},
                {"user_id": 999, "cost": 100.0, "save": 0, "discounts": []},
            ],
        },
        "2617251613",
    )

    assert values["seller_cost"] == Decimal("8.25")
    assert values["cost_save"] == Decimal("3.54")
    assert values["costs_gross_amount"] == Decimal("29.69")


def test_shipment_costs_without_our_sender_is_unknown_not_zero() -> None:
    values = parse.shipment_costs({"senders": [{"user_id": 999, "cost": 5.0}]}, "2617251613")
    assert values["seller_cost"] is None


def test_shipment_row_ignores_addresses_and_receiver() -> None:
    row = parse.shipment_row(
        {
            "id": 48367189736,
            "order_id": 2000019209106214,
            "status": "shipped",
            "mode": "me2",
            "shipping_mode": None,
            "logistic_type": "xd_drop_off",
            "base_cost": 11.79,
            "cost_components": {"ratio": 8.25, "loyal_discount": 0.3},
            "receiver_address": {"street_name": "Rua Sigilosa", "zip_code": "00000-000"},
            "receiver_id": 123456789,
            "customer_id": 987654321,
        },
        NOW,
    )

    assert row["shipping_mode"] == "me2"
    assert row["order_id"] == 2000019209106214
    dumped = str(row)
    assert "Rua Sigilosa" not in dumped
    assert "123456789" not in dumped
    # Números de diagnóstico ficam como texto exato, nunca float.
    assert row["cost_components"] == {"ratio": "8.25", "loyal_discount": "0.3"}


def test_visits_parse_daily_points() -> None:
    rows = parse.visit_rows(
        {
            "total_visits": 1,
            "results": [{"date": "2026-10-22T00:00:00Z", "total": 1, "visits_detail": []}],
        }
    )
    assert rows == [(datetime(2026, 10, 22, tzinfo=UTC).date(), 1)]


def test_negative_visit_point_is_rejected() -> None:
    with pytest.raises(parse.BadPayload):
        parse.visit_rows({"results": [{"date": "2026-10-22T00:00:00Z", "total": -1}]})


def test_coverage_is_authoritative_only_for_active_item() -> None:
    payload = {
        "date_from": "2026-08-01T00:00:00Z",
        "date_to": "2026-10-30T00:00:00Z",
        "total_visits": 4,
        "results": [{}, {}, {}, {}],
    }

    active = parse.coverage_values(payload, item_status="active", window_days=90, now=NOW)
    closed = parse.coverage_values(payload, item_status="closed", window_days=90, now=NOW)

    assert active["authoritative"] is True
    assert active["points_returned"] == 4
    assert closed["authoritative"] is False


def test_numbers_as_text_keeps_exact_decimal() -> None:
    assert numbers_as_text({"ratio": 8.25, "nested": [0.1], "ok": True}) == {
        "ratio": "8.25",
        "nested": ["0.1"],
        "ok": True,
    }
