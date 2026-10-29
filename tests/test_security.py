from __future__ import annotations

import pytest

from seller_radar.security import (
    csrf_matches,
    hash_password,
    needs_rehash,
    new_csrf_token,
    verify_password,
)


def test_password_hash_is_verifiable_and_unique() -> None:
    first = hash_password("senha-super-secreta")
    second = hash_password("senha-super-secreta")

    assert first != second, "o salt precisa tornar cada hash diferente"
    assert verify_password(first, "senha-super-secreta")
    assert not verify_password(first, "senha-errada")


def test_hash_of_current_password_does_not_need_rehash() -> None:
    assert needs_rehash(hash_password("senha-super-secreta")) is False


def test_garbage_hash_never_authenticates() -> None:
    assert verify_password("nao-e-um-hash", "qualquer") is False
    assert needs_rehash("nao-e-um-hash") is True


def test_csrf_tokens_are_unique_and_compared_in_constant_time() -> None:
    first = new_csrf_token()
    second = new_csrf_token()

    assert first != second
    assert csrf_matches(first, first)
    assert not csrf_matches(first, second)
    assert not csrf_matches(None, first)
    assert not csrf_matches(first, None)


@pytest.mark.parametrize("value", ["", " ", "0" * 43])
def test_short_or_empty_csrf_is_rejected(value: str) -> None:
    assert not csrf_matches(new_csrf_token(), value)
