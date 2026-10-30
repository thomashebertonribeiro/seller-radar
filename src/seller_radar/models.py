"""Modelo de dados.

Fase 1: usuário, credencial do Mercado Livre e registro de execução.
Fase 3b: catálogo, pedidos, envios, visitas e custos com vigência.

Duas regras atravessam o arquivo:

1. **Nada de JSON cru de payload.** As respostas de `/items`, `/orders` e
   `/shipments` trazem endereço do comprador, `receiver_id`, `customer_id` e
   contato do vendedor. Só colunas escolhidas são gravadas; o payload bruto nunca
   entra no banco.
2. **Dinheiro é `NUMERIC`, nunca `float`** (D8). `unit_cost` usa 4 casas porque é
   custo por unidade — dividir o custo de uma caixa por 100 unidades produz
   fração de centavo, e arredondar na entrada distorceria a margem.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from seller_radar.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    ml_credential: Mapped[MlCredential | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class MlCredential(Base):
    """Tokens do Mercado Livre, sempre criptografados.

    O refresh token é de uso único: renovação exige lock e gravação atômica
    (ver `ml/credentials.py`). Quando o provedor recusa o refresh, a credencial
    é marcada com `needs_reconnect` em vez de ser reutilizada em laço.
    """

    __tablename__ = "ml_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    ml_user_id: Mapped[str | None] = mapped_column(String(32))
    ml_nickname: Mapped[str | None] = mapped_column(String(64))
    ml_site_id: Mapped[str | None] = mapped_column(String(8))
    access_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scope: Mapped[str | None] = mapped_column(Text)
    needs_reconnect: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    last_error: Mapped[str | None] = mapped_column(String(120))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=utcnow, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="ml_credential")


class OAuthState(Base):
    """`state` de autorização, de uso único e com validade curta.

    Fica no banco, não na memória: um restart do `web` no meio do fluxo não pode
    transformar uma autorização legítima em erro, nem permitir reuso do `state`.
    O verificador PKCE é guardado cifrado, com a mesma chave dos tokens.
    """

    __tablename__ = "oauth_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    state: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code_verifier_enc: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SyncRun(Base):
    """Execução (agendada ou manual), com checkpoint para retomada idempotente."""

    __tablename__ = "sync_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'success', 'failed', 'skipped')",
            name="ck_sync_runs_status",
        ),
        Index("ix_sync_runs_kind_started_at", "kind", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    mode: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Contadores do que foi gravado, não do que foi pedido.
    items_synced: Mapped[int | None] = mapped_column(Integer)
    orders_synced: Mapped[int | None] = mapped_column(Integer)
    visits_synced: Mapped[int | None] = mapped_column(Integer)
    api_calls: Mapped[int | None] = mapped_column(Integer)
    checkpoint: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[list[str] | None] = mapped_column(ARRAY(Text))


class Item(Base):
    """Anúncio do vendedor, como a API o descreve no momento da coleta.

    `sold_quantity` é acumulado do anúncio e não substitui a soma dos pedidos:
    pedidos podem ser cancelados e o campo não retrocede.
    """

    __tablename__ = "items"
    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_items_price"),
        CheckConstraint("available_quantity >= 0", name="ck_items_available_quantity"),
        Index("ix_items_status", "status"),
        Index("ix_items_seller_sku", "seller_sku"),
    )

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    seller_id: Mapped[str | None] = mapped_column(String(32), index=True)
    title: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(24))
    sub_status: Mapped[list[str] | None] = mapped_column(ARRAY(String(32)))
    category_id: Mapped[str | None] = mapped_column(String(24))
    # É um slug de taxonomia (`MLB-MANUAL_GARLIC_PRESSES_AND_CHOPPERS`, 38
    # caracteres medidos), não um id de formato fixo. Sem tamanho documentado
    # pelo provedor, `text` evita que o próximo domínio mais longo derrube a
    # coleta inteira — foi exatamente o que aconteceu com `varchar(24)`.
    domain_id: Mapped[str | None] = mapped_column(Text)
    listing_type_id: Mapped[str | None] = mapped_column(String(24))
    condition: Mapped[str | None] = mapped_column(String(24))
    catalog_listing: Mapped[bool | None] = mapped_column(Boolean)
    catalog_product_id: Mapped[str | None] = mapped_column(String(32))
    price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    base_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    original_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    currency_id: Mapped[str | None] = mapped_column(String(8))
    available_quantity: Mapped[int | None] = mapped_column(Integer)
    sold_quantity: Mapped[int | None] = mapped_column(Integer)
    initial_quantity: Mapped[int | None] = mapped_column(Integer)
    # O SKU público do anúncio vem em `seller_custom_field` (pode ser nulo).
    seller_sku: Mapped[str | None] = mapped_column(String(128))
    permalink: Mapped[str | None] = mapped_column(Text)
    date_created: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stop_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ItemVisit(Base):
    """Visitas de um anúncio em um dia.

    O Mercado Livre só devolve os dias com visita. A ausência de linha só pode ser
    lida como zero quando existe cobertura **autoritativa** para aquele anúncio
    (ver `ItemVisitCoverage`) — para anúncio encerrado não há coleta.
    """

    __tablename__ = "item_visits"
    __table_args__ = (CheckConstraint("visits >= 0", name="ck_item_visits_visits"),)

    item_id: Mapped[str] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    visit_date: Mapped[date] = mapped_column(Date, primary_key=True)
    visits: Mapped[int] = mapped_column(Integer, nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ItemVisitCoverage(Base):
    """Registro de que uma janela de visitas foi consultada — e com qual resultado.

    Sem esta tabela, "zero visitas" e "nunca coletado" seriam indistinguíveis.
    `authoritative` só é verdadeiro quando o anúncio estava ativo na coleta: nesse
    caso a janela substitui os dados anteriores; para anúncio inativo o histórico
    coletado enquanto estava ativo é preservado.
    """

    __tablename__ = "item_visit_coverages"

    item_id: Mapped[str] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    window_days: Mapped[int] = mapped_column(Integer, primary_key=True)
    covered_from: Mapped[date | None] = mapped_column(Date)
    covered_to: Mapped[date | None] = mapped_column(Date)
    item_status: Mapped[str | None] = mapped_column(String(24))
    authoritative: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    points_returned: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    total_reported: Mapped[int | None] = mapped_column(Integer)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Order(Base):
    """Pedido. `shipping_cost` do pedido é sempre nulo na API: não é coluna aqui.

    O frete real vive em `shipments.seller_cost`, coletado de
    `/shipments/{id}/costs`; a tarifa do Mercado Livre vive em
    `order_items.sale_fee_unit`, que é **por unidade** (medido em 29/10/2026).
    """

    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("total_amount >= 0", name="ck_orders_total_amount"),
        Index("ix_orders_date_created", "date_created"),
        Index("ix_orders_shipping_id", "shipping_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    seller_id: Mapped[str | None] = mapped_column(String(32), index=True)
    status: Mapped[str | None] = mapped_column(String(32))
    date_created: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    date_closed: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    date_last_updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    paid_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    currency_id: Mapped[str | None] = mapped_column(String(8))
    # Só o id: é o suficiente para reconhecer recompra. Apelido, e-mail e
    # endereço do comprador não são armazenados.
    buyer_id: Mapped[str | None] = mapped_column(String(32))
    shipping_id: Mapped[int | None] = mapped_column(BigInteger)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String(48)))
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OrderItem(Base):
    """Linha de pedido.

    `sale_fee_unit` é a tarifa **por unidade** informada pela API; a tarifa da
    linha é `sale_fee_unit * quantity`, calculada na leitura. Guardar o valor
    bruto da API evita inventar uma multiplicação que o provedor não fez.
    """

    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_items_quantity"),
        CheckConstraint("unit_price >= 0", name="ck_order_items_unit_price"),
        CheckConstraint("sale_fee_unit >= 0", name="ck_order_items_sale_fee"),
        CheckConstraint(
            "freight_allocated IS NULL OR freight_allocated >= 0",
            name="ck_order_items_freight",
        ),
        Index("ix_order_items_item_id", "item_id"),
    )

    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[str | None] = mapped_column(String(20))
    title: Mapped[str | None] = mapped_column(Text)
    seller_sku: Mapped[str | None] = mapped_column(String(128))
    listing_type_id: Mapped[str | None] = mapped_column(String(24))
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    sale_fee_unit: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0")
    )
    currency_id: Mapped[str | None] = mapped_column(String(8))
    # Rateio do frete do envio entre as linhas do pedido. `NULL` = não rateado
    # (envio sem custo conhecido); `freight_estimated` marca rateio aproximado.
    freight_allocated: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    freight_estimated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class Payment(Base):
    """Pagamento do pedido, identificado pela posição no array da API."""

    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("installments > 0", name="ck_payments_installments"),
        CheckConstraint(
            "total_paid_amount IS NULL OR total_paid_amount >= 0",
            name="ck_payments_total_paid",
        ),
    )

    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    payment_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str | None] = mapped_column(String(32))
    payment_type: Mapped[str | None] = mapped_column(String(32))
    payment_method_id: Mapped[str | None] = mapped_column(String(32))
    installments: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    total_paid_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    transaction_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    # Medido em 29/10/2026: `marketplace_fee` vem `null` ou `0.0` e não serve
    # como fonte de tarifa. Fica gravado para auditoria, não para cálculo.
    marketplace_fee: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    currency_id: Mapped[str | None] = mapped_column(String(8))


class Shipment(Base):
    """Envio e custo real do frete para o vendedor.

    O payload de `/shipments/{id}` traz endereço do destinatário, `receiver_id` e
    `customer_id`; nenhum deles é coluna. O que importa financeiramente é
    `seller_cost` (soma de `senders[].cost` do nosso vendedor em
    `/shipments/{id}/costs`), que é o valor efetivamente pago.
    """

    __tablename__ = "shipments"
    __table_args__ = (
        CheckConstraint("seller_cost IS NULL OR seller_cost >= 0", name="ck_shipments_seller_cost"),
        Index("ix_shipments_order_id", "order_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str | None] = mapped_column(String(32))
    shipping_mode: Mapped[str | None] = mapped_column(String(24))
    logistic_type: Mapped[str | None] = mapped_column(String(32))
    base_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    list_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    seller_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    costs_gross_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    cost_save: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    # `cost_components` é um dicionário pequeno de razões e descontos, sem PII.
    cost_components: Mapped[dict | None] = mapped_column(JSONB)
    currency_id: Mapped[str | None] = mapped_column(String(8))
    costs_collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    allocated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    allocated_lines: Mapped[int | None] = mapped_column(Integer)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProductCost(Base):
    """Custo unitário com vigência, para o custo histórico não ser reescrito.

    A exclusão de sobreposição é feita no banco (`EXCLUDE USING gist`): duas
    linhas válidas no mesmo dia para o mesmo anúncio tornariam a margem
    ambígua, e um erro de cadastro não pode virar número silenciosamente errado.
    """

    __tablename__ = "product_costs"
    __table_args__ = (
        CheckConstraint("unit_cost >= 0", name="ck_product_costs_unit_cost"),
        CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from", name="ck_product_costs_range"
        ),
        UniqueConstraint("item_id", "valid_from", name="uq_product_costs_item_from"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[str] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
