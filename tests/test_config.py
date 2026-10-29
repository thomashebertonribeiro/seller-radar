from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from seller_radar.config import Settings


def _base(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "secret_key": "x" * 40,
        "database_url": "postgresql+psycopg://radar:radar@localhost:5432/radar",
        "token_encryption_key": Fernet.generate_key().decode(),
    }
    data.update(overrides)
    return data


def test_rejects_invalid_encryption_key() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **_base(token_encryption_key="nao-e-base64"))  # type: ignore[call-arg]


def test_rejects_short_secret_key() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **_base(secret_key="curta"))  # type: ignore[call-arg]


@pytest.mark.parametrize("rate", ["-0.1", "1.5"])
def test_tax_rate_must_be_a_fraction(rate: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **_base(tax_rate=rate))  # type: ignore[call-arg]


def test_production_flag_and_ml_configuration() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        **_base(
            app_env="production",
            ml_client_id="1",
            ml_client_secret="2",
            ml_redirect_uri="https://ml.exemplo.com/callback",
        ),
    )

    assert settings.is_production is True
    assert settings.ml_configured is True

    without_ml = Settings(
        _env_file=None,  # type: ignore[call-arg]
        **_base(app_env="development"),
    )
    assert without_ml.is_production is False
    assert without_ml.ml_configured is False
