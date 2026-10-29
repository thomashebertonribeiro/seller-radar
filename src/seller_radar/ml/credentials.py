"""Credenciais do Mercado Livre: `state` de uso único e renovação de token.

Duas invariantes deste módulo:

1. **`state` é de uso único.** O consumo é um único `UPDATE ... RETURNING` que
   só acerta a linha se ela ainda não foi consumida e não expirou. Duas
   requisições simultâneas com o mesmo `state` — replayed ou duplicadas pelo
   navegador — resultam em uma única troca de código.
2. **A renovação é serializada.** O refresh token do Mercado Livre é de uso
   único: duas renovações concorrentes fazem o provedor rotacionar o token duas
   vezes e uma delas fica inválida, derrubando a conexão. Por isso a renovação
   roda sob `pg_advisory_xact_lock`, com o novo par gravado na mesma transação.

Nada aqui faz `POST` fora de `ml/oauth.py`, que é a única exceção auditável.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from seller_radar.config import Settings
from seller_radar.crypto import TokenCipher
from seller_radar.ml.oauth import OAuthError, TokenSet, new_code_verifier, new_state, refresh
from seller_radar.models import MlCredential, OAuthState, utcnow

# Renova antes de expirar: evita corrida entre a checagem e o uso.
REFRESH_MARGIN = timedelta(minutes=5)

# Validade do `state`: tempo suficiente para o login no Mercado Livre, curto o
# bastante para que um `state` vazado não sirva depois.
STATE_TTL = timedelta(minutes=10)

# Namespace do advisory lock — separa estas travas de qualquer outra do banco.
ADVISORY_NAMESPACE = 7300

# Erros em que insistir não adianta: a conta precisa ser autorizada de novo.
TERMINAL_OAUTH_ERRORS = frozenset({"invalid_grant", "invalid_client", "invalid_token"})


class CredentialMissing(RuntimeError):
    """Não há credencial do Mercado Livre para o usuário."""


class ReconnectRequired(RuntimeError):
    """A credencial existe, mas precisa de nova autorização do vendedor."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"reconexão necessária: {reason}")
        self.reason = reason


class RefreshFailed(RuntimeError):
    def __init__(self, error: str, description: str | None) -> None:
        super().__init__(f"falha ao renovar o token: {error}")
        self.error = error
        self.description = description


@dataclass(frozen=True)
class PendingAuthorization:
    state: str
    code_verifier: str


def needs_refresh(
    expires_at: datetime, now: datetime, *, margin: timedelta = REFRESH_MARGIN
) -> bool:
    return expires_at - now <= margin


def begin_authorization(
    db: Session, user_id: int, cipher: TokenCipher, *, now: datetime | None = None
) -> PendingAuthorization:
    """Cria o `state` e o verificador PKCE, guardando só o que é preciso."""
    now = now or utcnow()
    verifier = new_code_verifier()
    pending = PendingAuthorization(state=new_state(), code_verifier=verifier)

    # Limpeza oportunista: `state` vencido não precisa de histórico.
    db.execute(OAuthState.__table__.delete().where(OAuthState.expires_at < now - timedelta(days=1)))
    db.add(
        OAuthState(
            state=pending.state,
            user_id=user_id,
            code_verifier_enc=cipher.encrypt(verifier),
            expires_at=now + STATE_TTL,
        )
    )
    db.commit()
    return pending


def consume_state(
    db: Session, state: str, user_id: int, cipher: TokenCipher, *, now: datetime | None = None
) -> str | None:
    """Consome o `state` atomicamente e devolve o verificador PKCE.

    Retorna `None` para: `state` desconhecido, de outro usuário, já consumido ou
    expirado. Os quatro casos são indistinguíveis por opção — a resposta ao
    navegador não deve ajudar quem está sondando.
    """
    now = now or utcnow()
    stmt = (
        update(OAuthState)
        .where(
            OAuthState.state == state,
            OAuthState.user_id == user_id,
            OAuthState.consumed_at.is_(None),
            OAuthState.expires_at > now,
        )
        .values(consumed_at=now)
        .returning(OAuthState.code_verifier_enc)
    )
    encrypted = db.execute(stmt).scalar_one_or_none()
    db.commit()
    if encrypted is None:
        return None
    return cipher.decrypt(encrypted)


def store_tokens(
    db: Session,
    user_id: int,
    tokens: TokenSet,
    cipher: TokenCipher,
    *,
    nickname: str | None = None,
    site_id: str | None = None,
) -> MlCredential:
    """Grava (ou substitui) o par de tokens do usuário."""
    credential = db.scalar(select(MlCredential).where(MlCredential.user_id == user_id))
    if credential is None:
        credential = MlCredential(user_id=user_id)
        db.add(credential)

    credential.access_token_enc = cipher.encrypt(tokens.access_token)
    credential.refresh_token_enc = cipher.encrypt(tokens.refresh_token)
    credential.expires_at = tokens.expires_at
    credential.scope = tokens.scope
    if tokens.ml_user_id:
        credential.ml_user_id = tokens.ml_user_id
    if nickname:
        credential.ml_nickname = nickname
    if site_id:
        credential.ml_site_id = site_id
    credential.needs_reconnect = False
    credential.last_error = None

    db.commit()
    return credential


def mark_needs_reconnect(db: Session, user_id: int, reason: str) -> None:
    db.execute(
        update(MlCredential)
        .where(MlCredential.user_id == user_id)
        .values(needs_reconnect=True, last_error=reason[:120], updated_at=utcnow())
    )
    db.commit()


def get_access_token(
    db: Session,
    user_id: int,
    settings: Settings,
    cipher: TokenCipher,
    *,
    now: datetime | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """Devolve um access token válido, renovando se necessário.

    Caminho rápido sem lock: token com mais de `REFRESH_MARGIN` de vida. Fora
    disso, a renovação acontece sob advisory lock, e o token é relido já com
    `FOR UPDATE` — quem esperou encontra o token novo e não renova de novo.
    """
    now = now or utcnow()

    credential = db.scalar(select(MlCredential).where(MlCredential.user_id == user_id))
    if credential is None:
        raise CredentialMissing("conta do Mercado Livre não conectada")
    if credential.needs_reconnect:
        raise ReconnectRequired(credential.last_error or "credencial recusada pelo provedor")
    if not needs_refresh(credential.expires_at, now):
        return cipher.decrypt(credential.access_token_enc)

    db.execute(select(func.pg_advisory_xact_lock(ADVISORY_NAMESPACE, user_id)))

    credential = db.scalar(
        select(MlCredential).where(MlCredential.user_id == user_id).with_for_update()
    )
    if credential is None:
        db.rollback()
        raise CredentialMissing("conta do Mercado Livre não conectada")
    if not needs_refresh(credential.expires_at, now):
        # Outro processo renovou enquanto esperávamos o lock.
        db.commit()
        return cipher.decrypt(credential.access_token_enc)

    current_refresh_token = cipher.decrypt(credential.refresh_token_enc)
    try:
        # `asyncio.run` é seguro aqui: os endpoints são síncronos e rodam no
        # threadpool do Starlette, sem event loop ativo.
        tokens = asyncio.run(refresh(settings, current_refresh_token, transport=transport))
    except OAuthError as exc:
        db.rollback()
        if exc.error in TERMINAL_OAUTH_ERRORS:
            mark_needs_reconnect(db, user_id, exc.error)
            raise ReconnectRequired(exc.error) from exc
        raise RefreshFailed(exc.error, exc.description) from exc

    credential.access_token_enc = cipher.encrypt(tokens.access_token)
    credential.refresh_token_enc = cipher.encrypt(tokens.refresh_token)
    credential.expires_at = tokens.expires_at
    if tokens.scope:
        credential.scope = tokens.scope
    credential.last_error = None
    db.commit()
    return tokens.access_token
