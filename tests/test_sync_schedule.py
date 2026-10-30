"""Agendamento: leitura do horário e janela incremental."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from seller_radar.sync.engine import SyncOptions, orders_since
from seller_radar.worker import parse_run_time

NOW = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)


def test_parse_run_time_accepts_hh_mm() -> None:
    assert parse_run_time("06:00") == (6, 0)
    assert parse_run_time(" 23:59 ") == (23, 59)


@pytest.mark.parametrize("value", ["6", "24:00", "12:60", "seis", "", "12:aa"])
def test_parse_run_time_rejects_invalid(value: str) -> None:
    with pytest.raises(ValueError):
        parse_run_time(value)


def test_first_run_uses_the_initial_window() -> None:
    options = SyncOptions(initial_days=365)

    since = orders_since({}, now=NOW, options=options, full=False)

    assert since == NOW - timedelta(days=365)


def test_incremental_run_uses_watermark_with_overlap() -> None:
    options = SyncOptions(order_overlap=timedelta(hours=1))
    checkpoint = {"orders_since": (NOW - timedelta(days=2)).isoformat()}

    since = orders_since(checkpoint, now=NOW, options=options, full=False)

    assert since == NOW - timedelta(days=2, hours=1)


def test_full_mode_ignores_the_checkpoint() -> None:
    options = SyncOptions(initial_days=30)
    checkpoint = {"orders_since": (NOW - timedelta(days=2)).isoformat()}

    since = orders_since(checkpoint, now=NOW, options=options, full=True)

    assert since == NOW - timedelta(days=30)


def test_naive_watermark_is_read_as_utc() -> None:
    options = SyncOptions(order_overlap=timedelta(0))
    checkpoint = {"orders_since": "2026-10-01T12:00:00"}

    since = orders_since(checkpoint, now=NOW, options=options, full=False)

    assert since == datetime(2026, 10, 1, 12, tzinfo=UTC)
