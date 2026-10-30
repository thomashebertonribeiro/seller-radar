"""`items.domain_id` passa de varchar(24) para text

O primeiro sync real morreu no `INSERT INTO items`: `domain_id` é um slug de
taxonomia do provedor, não um id de formato fixo, e o maior valor medido nos 56
anúncios da conta tem 38 caracteres (`MLB-MANUAL_GARLIC_PRESSES_AND_CHOPPERS`).
A coluna de 24 recusava a linha inteira e nenhum anúncio era gravado.

Medição de 29/10/2026 — todos os outros `varchar` do catálogo, pedidos,
pagamentos e envios couberam com folga no tamanho atual (`status` 12/24,
`category_id` 9/24, `listing_type_id` 12/24, `condition` 4/24,
`payment_method_id` 16/32, `logistic_type` 12/32). Só esta coluna muda: alargar
o resto seria chute, não medição.

`text` em vez de um `varchar` maior porque o provedor não documenta o tamanho
máximo do domínio; o próximo slug mais longo não pode derrubar a coleta.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "items",
        "domain_id",
        existing_type=sa.String(length=24),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Trunca antes de encolher: sem isso o ALTER falha se houver domínio longo.
    # Mesmo critério da 0003 — o rótulo de taxonomia não é dado de negócio.
    op.alter_column(
        "items",
        "domain_id",
        existing_type=sa.Text(),
        type_=sa.String(length=24),
        existing_nullable=True,
        postgresql_using="left(domain_id, 24)",
    )
