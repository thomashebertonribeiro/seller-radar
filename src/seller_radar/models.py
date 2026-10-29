"""Modelo de dados inicial da Fase 1.

Deliberadamente pequeno: usuário, credencial do Mercado Livre e registro de
execução. Tabelas de anúncios, pedidos e custos entram quando os endpoints
correspondentes saírem de `pendente` em docs/api-validation.md.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
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
    scope: Mapped[str | None] = mapped_column(String(255))
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
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checkpoint: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
