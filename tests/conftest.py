"""Bootstrap do ambiente de teste, executado antes de importar a aplicação."""

from __future__ import annotations

import os

from cryptography.fernet import Fernet


def _set_default(name: str, value: str) -> None:
    os.environ.setdefault(name, value)


# A aplicação lê a configuração no import; os testes precisam de valores válidos
# e não devem depender de um .env local.
_set_default("APP_ENV", "test")
_set_default("SECRET_KEY", "t" * 48)
_set_default("DATABASE_URL", "postgresql+psycopg://radar:radar@localhost:5432/radar_test")
_set_default("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
_set_default("SESSION_COOKIE_SECURE", "false")

import httpx  # noqa: E402
import pytest  # noqa: E402

from seller_radar.config import Settings  # noqa: E402


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env="test",
        secret_key="s" * 48,
        database_url="postgresql+psycopg://radar:radar@localhost:5432/radar_test",
        token_encryption_key=Fernet.generate_key().decode(),
        session_cookie_secure=False,
        ml_client_id="123456",
        ml_client_secret="segredo",
        ml_redirect_uri="https://ml.exemplo.com/callback",
    )


def make_transport(responses: list[httpx.Response], captured: list[httpx.Request] | None = None):
    """Transporte falso que devolve as respostas na ordem e registra as requisições."""
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        return queue.pop(0)

    return httpx.MockTransport(handler)
