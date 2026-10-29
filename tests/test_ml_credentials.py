"""Regras puras da credencial: quando renovar e nada além disso."""

from __future__ import annotations

from datetime import timedelta

import pytest

from seller_radar.ml.credentials import REFRESH_MARGIN, STATE_TTL, needs_refresh
from seller_radar.models import utcnow


def test_token_with_room_to_spare_is_not_renewed() -> None:
    now = utcnow()

    assert not needs_refresh(now + timedelta(minutes=30), now)


def test_token_inside_the_margin_is_renewed() -> None:
    now = utcnow()

    assert needs_refresh(now + REFRESH_MARGIN - timedelta(seconds=1), now)
    assert needs_refresh(now + REFRESH_MARGIN, now)


def test_expired_token_is_renewed() -> None:
    now = utcnow()

    assert needs_refresh(now - timedelta(seconds=1), now)


@pytest.mark.parametrize("margin_minutes", [1, 5, 15])
def test_margin_is_configurable(margin_minutes: int) -> None:
    now = utcnow()
    margin = timedelta(minutes=margin_minutes)

    assert needs_refresh(now + margin, now, margin=margin)
    assert not needs_refresh(now + margin + timedelta(seconds=1), now, margin=margin)


def test_state_expires_faster_than_a_session() -> None:
    assert timedelta(minutes=15) >= STATE_TTL
