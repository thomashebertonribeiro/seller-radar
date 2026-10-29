"""Fluxo OAuth do Mercado Livre contra Postgres real.

Cobre o que não dá para provar sem banco: `state` de uso único (o consumo é um
`UPDATE ... RETURNING`), amarração do `state` ao usuário e renovação de token
serializada. As chamadas ao Mercado Livre são substituídas por funções falsas —
nenhum teste aqui fala com a internet.

    docker compose --profile test run --rm test pytest -q -m integration
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Iterator
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text

from seller_radar.config import get_settings
from seller_radar.crypto import TokenCipher
from seller_radar.db import SessionLocal
from seller_radar.ml import credentials
from seller_radar.ml.credentials import (
    CredentialMissing,
    ReconnectRequired,
    get_access_token,
    store_tokens,
)
from seller_radar.ml.oauth import OAuthError, TokenSet
from seller_radar.models import MlCredential, OAuthState, User, utcnow
from seller_radar.security import hash_password
from seller_radar.web import oauth_routes
from seller_radar.web.main import create_app

pytestmark = pytest.mark.integration

CSRF_PATTERN = re.compile(r'name="csrf_token" value="([^"]+)"')


def _client() -> TestClient:
    return TestClient(create_app(get_settings()), base_url="http://127.0.0.1:8010")


def _cipher() -> TokenCipher:
    return TokenCipher(get_settings().token_encryption_key)


def _create_account() -> dict[str, str | int]:
    email = f"oauth-{secrets.token_hex(4)}@local.invalid"
    password = secrets.token_urlsafe(24)
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))  # falha cedo se o banco não estiver disponível
        user = User(email=email, password_hash=hash_password(password))
        db.add(user)
        db.commit()
        user_id = user.id
    return {"email": email, "password": password, "id": user_id}


def _drop_account(email: str) -> None:
    with SessionLocal() as db:
        db.execute(delete(User).where(User.email == email))
        db.commit()


@pytest.fixture
def account() -> Iterator[dict[str, str | int]]:
    created = _create_account()
    try:
        yield created
    finally:
        _drop_account(str(created["email"]))


def _login(client: TestClient, account: dict[str, str | int]) -> None:
    token = CSRF_PATTERN.search(client.get("/login").text)
    assert token is not None
    response = client.post(
        "/login",
        data={
            "email": account["email"],
            "password": account["password"],
            "csrf_token": token.group(1),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def _csrf(client: TestClient) -> str:
    match = CSRF_PATTERN.search(client.get("/").text)
    assert match is not None
    return match.group(1)


def _start(client: TestClient) -> str:
    """Inicia o fluxo e devolve o `state` emitido."""
    response = client.post(
        "/auth/mercadolivre/start",
        data={"csrf_token": _csrf(client)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("https://auth.mercadolivre.com.br/authorization?")
    return parse_qs(urlparse(location).query)["state"][0]


def _tokens(access: str = "APP_USR-teste", refresh_token: str = "TG-teste") -> TokenSet:
    return TokenSet(
        access_token=access,
        refresh_token=refresh_token,
        expires_at=utcnow() + timedelta(hours=6),
        scope="offline_access read",
        ml_user_id="123456",
    )


def test_start_creates_pending_state_bound_to_the_user(account: dict[str, str | int]) -> None:
    client = _client()
    _login(client, account)

    state = _start(client)

    with SessionLocal() as db:
        row = db.scalar(select(OAuthState).where(OAuthState.state == state))

    assert row is not None
    assert row.user_id == account["id"]
    assert row.consumed_at is None
    assert row.expires_at > utcnow()
    verifier = _cipher().decrypt(row.code_verifier_enc)
    assert 43 <= len(verifier) <= 128


def test_start_without_csrf_is_refused(account: dict[str, str | int]) -> None:
    client = _client()
    _login(client, account)

    response = client.post("/auth/mercadolivre/start", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/?ml=csrf"
    with SessionLocal() as db:
        assert db.scalars(select(OAuthState).where(OAuthState.user_id == account["id"])).all() == []


def test_callback_stores_encrypted_tokens(
    account: dict[str, str | int], monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client()
    _login(client, account)
    seen: list[tuple[str, str | None]] = []

    async def fake_exchange(settings, code, *, code_verifier=None, transport=None):
        seen.append((code, code_verifier))
        return _tokens()

    monkeypatch.setattr(oauth_routes, "exchange_code", fake_exchange)
    monkeypatch.setattr(oauth_routes, "_profile", lambda token: ("loja-teste", "MLB"))

    state = _start(client)
    response = client.get(
        f"/auth/mercadolivre/callback?code=codigo-1&state={state}", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/?ml=conectado"
    assert len(seen) == 1
    assert seen[0][1], "o verificador PKCE precisa acompanhar a troca do código"

    with SessionLocal() as db:
        credential = db.scalar(select(MlCredential).where(MlCredential.user_id == account["id"]))

    assert credential is not None
    assert "APP_USR-teste" not in credential.access_token_enc
    assert "TG-teste" not in credential.refresh_token_enc
    assert _cipher().decrypt(credential.access_token_enc) == "APP_USR-teste"
    assert _cipher().decrypt(credential.refresh_token_enc) == "TG-teste"
    assert credential.ml_nickname == "loja-teste"
    assert credential.ml_site_id == "MLB"
    assert credential.needs_reconnect is False


def test_callback_cannot_be_replayed(
    account: dict[str, str | int], monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client()
    _login(client, account)
    calls = 0

    async def fake_exchange(settings, code, *, code_verifier=None, transport=None):
        nonlocal calls
        calls += 1
        return _tokens()

    monkeypatch.setattr(oauth_routes, "exchange_code", fake_exchange)
    monkeypatch.setattr(oauth_routes, "_profile", lambda token: (None, None))

    state = _start(client)
    first = client.get(f"/auth/mercadolivre/callback?code=c&state={state}", follow_redirects=False)
    second = client.get(f"/auth/mercadolivre/callback?code=c&state={state}", follow_redirects=False)

    assert first.headers["location"] == "/?ml=conectado"
    assert second.headers["location"] == "/?ml=state"
    assert calls == 1


def test_state_from_another_account_is_refused(
    account: dict[str, str | int], monkeypatch: pytest.MonkeyPatch
) -> None:
    other = _create_account()
    try:
        door = _client()
        _login(door, account)
        state = _start(door)

        intruder = _client()
        _login(intruder, other)
        called = False

        async def fake_exchange(settings, code, *, code_verifier=None, transport=None):
            nonlocal called
            called = True
            return _tokens()

        monkeypatch.setattr(oauth_routes, "exchange_code", fake_exchange)

        response = intruder.get(
            f"/auth/mercadolivre/callback?code=c&state={state}", follow_redirects=False
        )

        assert response.headers["location"] == "/?ml=state"
        assert called is False
    finally:
        _drop_account(str(other["email"]))


def test_provider_refusal_returns_to_panel(account: dict[str, str | int]) -> None:
    client = _client()
    _login(client, account)

    response = client.get(
        "/auth/mercadolivre/callback?error=access_denied&state=qualquer", follow_redirects=False
    )

    assert response.headers["location"] == "/?ml=negado"


def test_disconnect_removes_the_credential(
    account: dict[str, str | int], monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client()
    _login(client, account)

    async def fake_exchange(settings, code, *, code_verifier=None, transport=None):
        return _tokens()

    monkeypatch.setattr(oauth_routes, "exchange_code", fake_exchange)
    monkeypatch.setattr(oauth_routes, "_profile", lambda token: (None, None))

    state = _start(client)
    client.get(f"/auth/mercadolivre/callback?code=c&state={state}", follow_redirects=False)

    response = client.post(
        "/auth/mercadolivre/disconnect",
        data={"csrf_token": _csrf(client)},
        follow_redirects=False,
    )

    assert response.headers["location"] == "/?ml=desconectado"
    with SessionLocal() as db:
        assert db.scalar(select(MlCredential).where(MlCredential.user_id == account["id"])) is None


def _seed_credential(
    user_id: int, *, expires_in: timedelta, access: str = "A1", refresh_token: str = "R1"
) -> None:
    with SessionLocal() as db:
        store_tokens(
            db,
            user_id,
            TokenSet(
                access_token=access,
                refresh_token=refresh_token,
                expires_at=utcnow() + expires_in,
                scope="offline_access read",
                ml_user_id="123456",
            ),
            _cipher(),
        )


def test_expiring_token_is_renewed_once_and_rotated(
    account: dict[str, str | int], monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_credential(int(account["id"]), expires_in=timedelta(seconds=30))
    used: list[str] = []

    async def fake_refresh(settings, refresh_token, *, transport=None):
        used.append(refresh_token)
        return TokenSet(
            access_token="A2",
            refresh_token="R2",
            expires_at=utcnow() + timedelta(hours=6),
            scope="offline_access read",
            ml_user_id="123456",
        )

    monkeypatch.setattr(credentials, "refresh", fake_refresh)

    with SessionLocal() as db:
        assert get_access_token(db, int(account["id"]), get_settings(), _cipher()) == "A2"
    with SessionLocal() as db:
        assert get_access_token(db, int(account["id"]), get_settings(), _cipher()) == "A2"

    assert used == ["R1"], "a segunda chamada deve usar o token já renovado"
    with SessionLocal() as db:
        credential = db.scalar(select(MlCredential).where(MlCredential.user_id == account["id"]))
    assert credential is not None
    assert _cipher().decrypt(credential.refresh_token_enc) == "R2"


def test_valid_token_never_calls_the_provider(
    account: dict[str, str | int], monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_credential(int(account["id"]), expires_in=timedelta(hours=3))

    async def explode(*args, **kwargs):
        raise AssertionError("não deveria renovar um token válido")

    monkeypatch.setattr(credentials, "refresh", explode)

    with SessionLocal() as db:
        assert get_access_token(db, int(account["id"]), get_settings(), _cipher()) == "A1"


def test_invalid_grant_marks_reconnect_without_retrying(
    account: dict[str, str | int], monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_credential(int(account["id"]), expires_in=timedelta(minutes=1))
    calls = 0

    async def refused(settings, refresh_token, *, transport=None):
        nonlocal calls
        calls += 1
        raise OAuthError("invalid_grant", "refresh token expirado")

    monkeypatch.setattr(credentials, "refresh", refused)

    with SessionLocal() as db, pytest.raises(ReconnectRequired):
        get_access_token(db, int(account["id"]), get_settings(), _cipher())
    with SessionLocal() as db, pytest.raises(ReconnectRequired):
        get_access_token(db, int(account["id"]), get_settings(), _cipher())

    assert calls == 1, "credencial marcada não deve ser tentada de novo"
    with SessionLocal() as db:
        credential = db.scalar(select(MlCredential).where(MlCredential.user_id == account["id"]))
    assert credential is not None
    assert credential.needs_reconnect is True
    assert credential.last_error == "invalid_grant"


def test_missing_credential_is_reported(account: dict[str, str | int]) -> None:
    with SessionLocal() as db, pytest.raises(CredentialMissing):
        get_access_token(db, int(account["id"]), get_settings(), _cipher())
