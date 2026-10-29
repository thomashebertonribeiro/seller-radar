from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from seller_radar.crypto import TokenCipher


def test_roundtrip() -> None:
    cipher = TokenCipher(Fernet.generate_key().decode())
    secret = "APP_USR-123456-abcdef"

    assert cipher.decrypt(cipher.encrypt(secret)) == secret


def test_ciphertext_does_not_contain_plaintext() -> None:
    cipher = TokenCipher(Fernet.generate_key().decode())

    assert "APP_USR" not in cipher.encrypt("APP_USR-123456")


def test_wrong_key_fails_loudly() -> None:
    escrito = TokenCipher(Fernet.generate_key().decode())
    outro = TokenCipher(Fernet.generate_key().decode())

    with pytest.raises(ValueError):
        outro.decrypt(escrito.encrypt("token"))
