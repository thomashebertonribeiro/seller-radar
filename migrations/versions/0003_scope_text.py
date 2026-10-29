"""`scope` do Mercado Livre passa de varchar(255) para text

O provedor devolve o escopo efetivo completo, não o que foi pedido: dezenas de
permissões `urn:ml:...` que passam de 800 caracteres. A coluna de 255 recusava a
gravação e o callback terminava em erro 500 — depois de o token já ter sido
emitido, o que obrigava a autorizar de novo.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "ml_credentials",
        "scope",
        existing_type=sa.String(length=255),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Trunca antes de encolher: sem isso o ALTER falha se houver escopo longo.
    op.alter_column(
        "ml_credentials",
        "scope",
        existing_type=sa.Text(),
        type_=sa.String(length=255),
        existing_nullable=True,
        postgresql_using="left(scope, 255)",
    )
