"""OAuth do Mercado Livre: `state` de uso único e estado da credencial

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ml_credentials", sa.Column("ml_nickname", sa.String(length=64), nullable=True))
    op.add_column("ml_credentials", sa.Column("ml_site_id", sa.String(length=8), nullable=True))
    op.add_column(
        "ml_credentials",
        sa.Column(
            "needs_reconnect",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column("ml_credentials", sa.Column("last_error", sa.String(length=120), nullable=True))

    op.create_table(
        "oauth_states",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("state", sa.String(length=128), nullable=False),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code_verifier_enc", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("state", name="uq_oauth_states_state"),
    )
    op.create_index("ix_oauth_states_user_id", "oauth_states", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_oauth_states_user_id", table_name="oauth_states")
    op.drop_table("oauth_states")
    op.drop_column("ml_credentials", "last_error")
    op.drop_column("ml_credentials", "needs_reconnect")
    op.drop_column("ml_credentials", "ml_site_id")
    op.drop_column("ml_credentials", "ml_nickname")
