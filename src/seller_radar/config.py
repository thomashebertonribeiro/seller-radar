"""Configuração carregada do ambiente, com validação que falha cedo.

Regra do projeto: nenhum valor padrão para segredo. Se `SECRET_KEY` ou
`TOKEN_ENCRYPTION_KEY` estiverem ausentes ou malformadas, a aplicação não sobe.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache

from cryptography.fernet import Fernet
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Aplicação ---
    app_env: str = "development"
    app_base_url: str = "http://localhost:8010"
    secret_key: str = Field(min_length=32)
    session_cookie_secure: bool = True

    # --- Banco ---
    database_url: str

    # --- Criptografia dos tokens do Mercado Livre ---
    token_encryption_key: str

    # --- Mercado Livre ---
    ml_client_id: str = ""
    ml_client_secret: str = ""
    ml_redirect_uri: str = ""

    # --- Claude API ---
    anthropic_api_key: str = ""
    claude_model: str = ""
    claude_daily_token_limit: int = 0

    # --- Parâmetros financeiros ---
    tax_rate: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    monthly_fixed_costs: Decimal = Field(default=Decimal("0"), ge=0)
    target_margin: Decimal = Field(default=Decimal("0"), ge=0, lt=1)

    # --- Agendamento ---
    daily_run_time: str = "06:00"
    timezone: str = "America/Sao_Paulo"

    # --- Backup ---
    backup_retention_days: int = 14

    @field_validator("token_encryption_key")
    @classmethod
    def _valid_fernet_key(cls, value: str) -> str:
        try:
            Fernet(value.encode())
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "TOKEN_ENCRYPTION_KEY precisa ser uma chave Fernet em base64 "
                "(gere com: python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\")"
            ) from exc
        return value

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}

    @property
    def ml_configured(self) -> bool:
        return bool(self.ml_client_id and self.ml_client_secret and self.ml_redirect_uri)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
