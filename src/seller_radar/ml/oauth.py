"""OAuth 2.0 do Mercado Livre.

Exceção única ao cliente somente leitura: `POST /oauth/token`, usado para trocar
o código de autorização e para renovar o par de tokens. Esta é a única função do
projeto autorizada a fazer `POST` contra a API, e ela existe em módulo separado
justamente para que a exceção seja explícita e auditável.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx

from seller_radar.config import Settings

AUTHORIZE_URL = "https://auth.mercadolivre.com.br/authorization"
TOKEN_URL = "https://api.mercadolibre.com/oauth/token"
SCOPE = "offline_access read"


class OAuthError(RuntimeError):
    def __init__(self, error: str, description: str | None = None) -> None:
        super().__init__(f"{error}: {description or 'sem descrição'}")
        self.error = error
        self.description = description


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    refresh_token: str
    expires_at: datetime
    scope: str | None
    ml_user_id: str | None


def new_state() -> str:
    """`state` aleatório de uso único.

    O Mercado Livre não valida o parâmetro; a validação é nossa (ver
    docs/api-validation.md), então ele precisa ser imprevisível e consumido uma
    única vez pela sessão.
    """
    return secrets.token_urlsafe(32)


def new_code_verifier() -> str:
    """Verificador PKCE (43 a 128 caracteres, RFC 7636)."""
    return secrets.token_urlsafe(64)


def code_challenge(verifier: str) -> str:
    """`code_challenge` = base64url(sha256(verifier)), sem padding."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def build_authorize_url(
    settings: Settings, state: str, *, pkce_challenge: str | None = None
) -> str:
    if not settings.ml_configured:
        raise OAuthError("configuracao_ausente", "ML_CLIENT_ID/SECRET/REDIRECT_URI não definidos")
    params = {
        "response_type": "code",
        "client_id": settings.ml_client_id,
        "redirect_uri": settings.ml_redirect_uri,
        "scope": SCOPE,
        "state": state,
    }
    if pkce_challenge:
        # Neutraliza interceptação do `code`: sem o verificador, o código não
        # pode ser trocado por tokens.
        params["code_challenge"] = pkce_challenge
        params["code_challenge_method"] = "S256"
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def _post_token(
    settings: Settings,
    payload: dict[str, str],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = 20.0,
) -> TokenSet:
    body = {
        **payload,
        "client_id": settings.ml_client_id,
        "client_secret": settings.ml_client_secret,
    }
    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
        response = await client.post(TOKEN_URL, data=body, headers={"Accept": "application/json"})

    if response.status_code != 200:
        try:
            data = response.json()
        except ValueError:
            data = {}
        raise OAuthError(
            data.get("error", f"http_{response.status_code}"),
            data.get("message") or data.get("error_description"),
        )

    data = response.json()
    return TokenSet(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        expires_at=datetime.now(UTC) + timedelta(seconds=int(data["expires_in"])),
        scope=data.get("scope"),
        ml_user_id=str(data["user_id"]) if data.get("user_id") is not None else None,
    )


async def exchange_code(
    settings: Settings,
    code: str,
    *,
    code_verifier: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> TokenSet:
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.ml_redirect_uri,
    }
    if code_verifier:
        payload["code_verifier"] = code_verifier
    return await _post_token(settings, payload, transport=transport)


async def refresh(
    settings: Settings,
    refresh_token: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> TokenSet:
    """Renova o par de tokens.

    O refresh token é de uso único: se esta chamada falhar depois de o provedor
    já ter rotacionado o token, a conta precisa ser reconectada. Por isso, na
    Fase 2, a chamada roda sob lock com gravação atômica do novo par.
    """
    return await _post_token(
        settings,
        {"grant_type": "refresh_token", "refresh_token": refresh_token},
        transport=transport,
    )
