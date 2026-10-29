"""Aplicação web (FastAPI + Jinja2).

Fase 1: login próprio, sessão assinada, proteção CSRF e painel vazio. Nenhuma
tela apresenta número: os indicadores dependem dos endpoints ainda marcados como
`pendente` em docs/api-validation.md.
"""

from __future__ import annotations

import time
from pathlib import Path
from threading import Lock

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from seller_radar.config import Settings, get_settings
from seller_radar.db import get_db
from seller_radar.models import User, utcnow
from seller_radar.security import (
    CSRF_SESSION_KEY,
    csrf_matches,
    needs_rehash,
    new_csrf_token,
    verify_password,
    hash_password,
)

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

SESSION_MAX_AGE_SECONDS = 12 * 60 * 60
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_ATTEMPTS = 5

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
}

_attempts: dict[str, list[float]] = {}
_attempts_lock = Lock()


def _client_ip(request: Request) -> str:
    # Sem proxy confiável configurado, `X-Forwarded-For` é falsificável.
    return request.client.host if request.client else "desconhecido"


def _throttled(ip: str, *, now: float | None = None) -> bool:
    now = now or time.monotonic()
    with _attempts_lock:
        window = [t for t in _attempts.get(ip, []) if now - t < LOGIN_WINDOW_SECONDS]
        _attempts[ip] = window
        return len(window) >= LOGIN_MAX_ATTEMPTS


def _register_failure(ip: str, *, now: float | None = None) -> None:
    now = now or time.monotonic()
    with _attempts_lock:
        _attempts.setdefault(ip, []).append(now)


def _clear_failures(ip: str) -> None:
    with _attempts_lock:
        _attempts.pop(ip, None)


def reset_login_throttle() -> None:
    with _attempts_lock:
        _attempts.clear()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title="Seller Radar",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="radar_session",
        max_age=SESSION_MAX_AGE_SECONDS,
        same_site="lax",
        https_only=settings.session_cookie_secure,
    )
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):  # noqa: ANN001, ANN202
        response: Response = await call_next(request)
        for header, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response

    def _render_login(request: Request, *, error: str | None = None, status_code: int = 200):
        token = request.session.get(CSRF_SESSION_KEY)
        if not token:
            token = new_csrf_token()
            request.session[CSRF_SESSION_KEY] = token
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": error, "csrf_token": token},
            status_code=status_code,
        )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request) -> Response:
        return _render_login(request)

    @app.post("/login")
    def login_submit(
        request: Request,
        email: str = Form(...),
        password: str = Form(...),
        csrf_token: str = Form(""),
        db: Session = Depends(get_db),
    ) -> Response:
        ip = _client_ip(request)

        if not csrf_matches(request.session.get(CSRF_SESSION_KEY), csrf_token):
            return _render_login(request, error="Sessão expirada. Tente novamente.", status_code=400)

        if _throttled(ip):
            return _render_login(
                request,
                error="Muitas tentativas. Aguarde 15 minutos.",
                status_code=429,
            )

        user = db.scalar(select(User).where(User.email == email.strip().lower()))

        if user is None or not verify_password(user.password_hash, password):
            _register_failure(ip)
            return _render_login(request, error="E-mail ou senha incorretos.", status_code=401)

        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)

        user.last_login_at = utcnow()
        db.commit()

        _clear_failures(ip)
        request.session.clear()
        request.session["user_id"] = user.id
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    def logout(request: Request, csrf_token: str = Form("")) -> Response:
        if not csrf_matches(request.session.get(CSRF_SESSION_KEY), csrf_token):
            return Response(status_code=400)
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, db: Session = Depends(get_db)) -> Response:
        user_id = request.session.get("user_id")
        if not user_id:
            return RedirectResponse("/login", status_code=303)

        user = db.get(User, user_id)
        if user is None:
            request.session.clear()
            return RedirectResponse("/login", status_code=303)

        token = request.session.get(CSRF_SESSION_KEY)
        if not token:
            token = new_csrf_token()
            request.session[CSRF_SESSION_KEY] = token

        credential = user.ml_credential
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "user": user,
                "csrf_token": token,
                "ml_connected": credential is not None,
                "ml_configured": settings.ml_configured,
            },
        )

    return app


app = create_app()
