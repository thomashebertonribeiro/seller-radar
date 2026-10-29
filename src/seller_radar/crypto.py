"""Criptografia simétrica dos tokens OAuth em repouso.

A chave vem do ambiente (`TOKEN_ENCRYPTION_KEY`) e nunca é gravada no banco.
Um dump do Postgres, sozinho, não entrega acesso à conta do vendedor.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class TokenCipher:
    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise ValueError(
                "token ilegível: chave de criptografia diferente da usada na gravação"
            ) from exc
