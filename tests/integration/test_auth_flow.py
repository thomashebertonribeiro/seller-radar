"""Fluxo de autenticação contra Postgres real.

Exige banco migrado; por isso fica atrás da marca `integration` e não roda na
suíte padrão. No servidor:

    docker compose --profile test run --rm test pytest -q -m integration
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, text

from seller_radar.config import get_settings
from seller_radar.db import SessionLocal
from seller_radar.models import User
from seller_radar.security import hash_password
from seller_radar.web.main import create_app

pytestmark = pytest.mark.integration

CSRF_PATTERN = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture
def user() -> Iterator[tuple[str, str]]:
    email = f"e2e-{secrets.token_hex(4)}@local.invalid"
    password = secrets.token_urlsafe(24)

    with SessionLocal() as db:
        db.execute(text("SELECT 1"))  # falha cedo se o banco não estiver disponível
        db.add(User(email=email, password_hash=hash_password(password)))
        db.commit()

    try:
        yield email, password
    finally:
        with SessionLocal() as db:
            db.execute(delete(User).where(User.email == email))
            db.commit()


def _client() -> TestClient:
    return TestClient(create_app(get_settings()), base_url="http://127.0.0.1:8010")


def test_wrong_password_is_rejected_and_right_one_opens_dashboard(user: tuple[str, str]) -> None:
    email, password = user
    client = _client()

    token = CSRF_PATTERN.search(client.get("/login").text)
    assert token is not None
    form = {"email": email, "csrf_token": token.group(1)}

    wrong = client.post("/login", data={**form, "password": "senha-errada"})
    assert wrong.status_code == 401
    assert "incorretos" in wrong.text

    ok = client.post("/login", data={**form, "password": password}, follow_redirects=False)
    assert ok.status_code == 303
    assert ok.headers["location"] == "/"

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert email in dashboard.text


def test_login_without_csrf_never_touches_the_database(user: tuple[str, str]) -> None:
    email, password = user
    client = _client()

    response = client.post("/login", data={"email": email, "password": password})

    assert response.status_code == 400
