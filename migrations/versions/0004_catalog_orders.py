"""catálogo, pedidos, envios, visitas e custos com vigência

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-29

Notas de modelagem (todas medidas com a conta real em 29/10/2026):

- `order_items.sale_fee_unit` guarda a tarifa **por unidade**, que é o que a API
  devolve; a tarifa da linha é o produto pela quantidade.
- `orders` não tem coluna de frete: `shipping_cost` do pedido vem sempre nulo.
  O frete real está em `shipments.seller_cost`.
- `product_costs` tem restrição de exclusão contra vigências sobrepostas, porque
  duas linhas válidas no mesmo dia tornariam a margem ambígua.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Necessária para `EXCLUDE USING gist (item_id WITH =, ...)`.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table(
        "items",
        sa.Column("id", sa.String(length=20), primary_key=True, nullable=False),
        sa.Column("seller_id", sa.String(length=32), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=True),
        sa.Column("sub_status", postgresql.ARRAY(sa.String(length=32)), nullable=True),
        sa.Column("category_id", sa.String(length=24), nullable=True),
        sa.Column("domain_id", sa.String(length=24), nullable=True),
        sa.Column("listing_type_id", sa.String(length=24), nullable=True),
        sa.Column("condition", sa.String(length=24), nullable=True),
        sa.Column("catalog_listing", sa.Boolean(), nullable=True),
        sa.Column("catalog_product_id", sa.String(length=32), nullable=True),
        sa.Column("price", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("base_price", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("original_price", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("currency_id", sa.String(length=8), nullable=True),
        sa.Column("available_quantity", sa.Integer(), nullable=True),
        sa.Column("sold_quantity", sa.Integer(), nullable=True),
        sa.Column("initial_quantity", sa.Integer(), nullable=True),
        sa.Column("seller_sku", sa.String(length=128), nullable=True),
        sa.Column("permalink", sa.Text(), nullable=True),
        sa.Column("date_created", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("price >= 0", name="ck_items_price"),
        sa.CheckConstraint("available_quantity >= 0", name="ck_items_available_quantity"),
    )
    op.create_index("ix_items_seller_id", "items", ["seller_id"])
    op.create_index("ix_items_status", "items", ["status"])
    op.create_index("ix_items_seller_sku", "items", ["seller_sku"])

    op.create_table(
        "item_visits",
        sa.Column("item_id", sa.String(length=20), nullable=False),
        sa.Column("visit_date", sa.Date(), nullable=False),
        sa.Column("visits", sa.Integer(), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"], ["items.id"], name="fk_item_visits_item", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("item_id", "visit_date"),
        sa.CheckConstraint("visits >= 0", name="ck_item_visits_visits"),
    )

    op.create_table(
        "item_visit_coverages",
        sa.Column("item_id", sa.String(length=20), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("covered_from", sa.Date(), nullable=True),
        sa.Column("covered_to", sa.Date(), nullable=True),
        sa.Column("item_status", sa.String(length=24), nullable=True),
        sa.Column("authoritative", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("points_returned", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("total_reported", sa.Integer(), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["items.id"],
            name="fk_item_visit_coverages_item",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("item_id", "window_days"),
    )

    op.create_table(
        "orders",
        sa.Column("id", sa.BigInteger(), primary_key=True, nullable=False),
        sa.Column("seller_id", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("date_created", sa.DateTime(timezone=True), nullable=True),
        sa.Column("date_closed", sa.DateTime(timezone=True), nullable=True),
        sa.Column("date_last_updated", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("paid_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("currency_id", sa.String(length=8), nullable=True),
        sa.Column("buyer_id", sa.String(length=32), nullable=True),
        sa.Column("shipping_id", sa.BigInteger(), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.String(length=48)), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("total_amount >= 0", name="ck_orders_total_amount"),
    )
    op.create_index("ix_orders_seller_id", "orders", ["seller_id"])
    op.create_index("ix_orders_date_created", "orders", ["date_created"])
    op.create_index("ix_orders_shipping_id", "orders", ["shipping_id"])

    op.create_table(
        "order_items",
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.String(length=20), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("seller_sku", sa.String(length=128), nullable=True),
        sa.Column("listing_type_id", sa.String(length=24), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "sale_fee_unit",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("currency_id", sa.String(length=8), nullable=True),
        sa.Column("freight_allocated", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column(
            "freight_estimated", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name="fk_order_items_order", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("order_id", "position"),
        sa.CheckConstraint("quantity > 0", name="ck_order_items_quantity"),
        sa.CheckConstraint("unit_price >= 0", name="ck_order_items_unit_price"),
        sa.CheckConstraint("sale_fee_unit >= 0", name="ck_order_items_sale_fee"),
        sa.CheckConstraint(
            "freight_allocated IS NULL OR freight_allocated >= 0",
            name="ck_order_items_freight",
        ),
    )
    op.create_index("ix_order_items_item_id", "order_items", ["item_id"])

    op.create_table(
        "payments",
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("payment_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("payment_type", sa.String(length=32), nullable=True),
        sa.Column("payment_method_id", sa.String(length=32), nullable=True),
        sa.Column("installments", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("total_paid_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("transaction_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("marketplace_fee", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("currency_id", sa.String(length=8), nullable=True),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name="fk_payments_order", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("order_id", "position"),
        sa.CheckConstraint("installments > 0", name="ck_payments_installments"),
        sa.CheckConstraint(
            "total_paid_amount IS NULL OR total_paid_amount >= 0",
            name="ck_payments_total_paid",
        ),
    )

    op.create_table(
        "shipments",
        sa.Column("id", sa.BigInteger(), primary_key=True, nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("shipping_mode", sa.String(length=24), nullable=True),
        sa.Column("logistic_type", sa.String(length=32), nullable=True),
        sa.Column("base_cost", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("list_cost", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("seller_cost", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("costs_gross_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("cost_save", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("cost_components", postgresql.JSONB(), nullable=True),
        sa.Column("currency_id", sa.String(length=8), nullable=True),
        sa.Column("costs_collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("allocated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("allocated_lines", sa.Integer(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "seller_cost IS NULL OR seller_cost >= 0", name="ck_shipments_seller_cost"
        ),
    )
    op.create_index("ix_shipments_order_id", "shipments", ["order_id"])

    op.create_table(
        "product_costs",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("item_id", sa.String(length=20), nullable=False),
        sa.Column("unit_cost", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["item_id"], ["items.id"], name="fk_product_costs_item", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id", "valid_from", name="uq_product_costs_item_from"),
        sa.CheckConstraint("unit_cost >= 0", name="ck_product_costs_unit_cost"),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from", name="ck_product_costs_range"
        ),
    )
    op.create_index("ix_product_costs_item_id", "product_costs", ["item_id"])
    op.execute(
        "ALTER TABLE product_costs ADD CONSTRAINT ex_product_costs_no_overlap "
        "EXCLUDE USING gist (item_id WITH =, daterange(valid_from, valid_to, '[]') WITH &&)"
    )

    # --- sync_runs: dono da execução, modo e contadores ---
    op.add_column("sync_runs", sa.Column("user_id", sa.Integer(), nullable=True))
    op.add_column("sync_runs", sa.Column("mode", sa.String(length=16), nullable=True))
    op.add_column("sync_runs", sa.Column("items_synced", sa.Integer(), nullable=True))
    op.add_column("sync_runs", sa.Column("orders_synced", sa.Integer(), nullable=True))
    op.add_column("sync_runs", sa.Column("visits_synced", sa.Integer(), nullable=True))
    op.add_column("sync_runs", sa.Column("api_calls", sa.Integer(), nullable=True))
    op.add_column("sync_runs", sa.Column("warnings", postgresql.ARRAY(sa.Text()), nullable=True))
    op.create_foreign_key(
        "fk_sync_runs_user", "sync_runs", "users", ["user_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_sync_runs_user_id", "sync_runs", ["user_id"])
    op.create_index("ix_sync_runs_kind_started_at", "sync_runs", ["kind", "started_at"])


def downgrade() -> None:
    op.drop_index("ix_sync_runs_kind_started_at", table_name="sync_runs")
    op.drop_index("ix_sync_runs_user_id", table_name="sync_runs")
    op.drop_constraint("fk_sync_runs_user", "sync_runs", type_="foreignkey")
    op.drop_column("sync_runs", "warnings")
    op.drop_column("sync_runs", "api_calls")
    op.drop_column("sync_runs", "visits_synced")
    op.drop_column("sync_runs", "orders_synced")
    op.drop_column("sync_runs", "items_synced")
    op.drop_column("sync_runs", "mode")
    op.drop_column("sync_runs", "user_id")

    op.drop_index("ix_product_costs_item_id", table_name="product_costs")
    op.drop_table("product_costs")  # a restrição de exclusão cai junto
    op.drop_index("ix_shipments_order_id", table_name="shipments")
    op.drop_table("shipments")
    op.drop_table("payments")
    op.drop_index("ix_order_items_item_id", table_name="order_items")
    op.drop_table("order_items")
    op.drop_index("ix_orders_shipping_id", table_name="orders")
    op.drop_index("ix_orders_date_created", table_name="orders")
    op.drop_index("ix_orders_seller_id", table_name="orders")
    op.drop_table("orders")
    op.drop_table("item_visit_coverages")
    op.drop_table("item_visits")
    op.drop_index("ix_items_seller_sku", table_name="items")
    op.drop_index("ix_items_status", table_name="items")
    op.drop_index("ix_items_seller_id", table_name="items")
    op.drop_table("items")
    # A extensão `btree_gist` fica instalada: removê-la poderia quebrar outro uso
    # no mesmo banco, e deixá-la não custa nada.
