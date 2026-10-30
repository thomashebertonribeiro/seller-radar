"""Renderização do painel com dados de sincronização.

Sem banco e sem HTTP: o alvo aqui é o Jinja. `{{ sync.items }}` não devolve a
contagem do dicionário — devolve o **método** `dict.items`, e o painel exibiu
"<built-in method items of dict object>" em produção. Este teste existe para
que o nome da chave não regresse.
"""

from __future__ import annotations

from types import SimpleNamespace

from seller_radar.web.main import templates


def _render(**sync_overrides: object) -> str:
    sync: dict[str, object] = {
        "last": None,
        "started": None,
        "duration_seconds": None,
        "item_count": 56,
        "items_active": 25,
        "orders": 48,
        "visit_days": 144,
        "freight_estimated_orders": 0,
        "daily_run_time": "06:00",
        "timezone": "America/Sao_Paulo",
    }
    sync.update(sync_overrides)

    return templates.env.get_template("dashboard.html").render(
        user=SimpleNamespace(email="dono@exemplo.com"),
        csrf_token="token",
        ml_connected=True,
        ml_configured=True,
        ml_nickname="BRASILICES",
        ml_needs_reconnect=False,
        message="",
        message_ok=True,
        sync=sync,
    )


def test_dashboard_renders_the_counts_as_numbers() -> None:
    html = _render()

    assert "<dd>56</dd>" in html, "o total de anúncios precisa sair como número"
    assert "<dd>25</dd>" in html
    assert "<dd>48</dd>" in html
    assert "<dd>144</dd>" in html
    assert "built-in method" not in html


def test_dashboard_without_any_run_explains_how_to_start() -> None:
    html = _render()

    assert "Nenhuma sincronização executada ainda" in html
    assert "python -m seller_radar.cli sync" in html


def test_dashboard_shows_warnings_and_the_overflow_count() -> None:
    last = SimpleNamespace(
        status="success",
        mode="incremental",
        error=None,
        warnings=["aviso 1", "aviso 2", "aviso 3", "aviso 4", "aviso 5", "aviso 6"],
    )

    html = _render(last=last, started="29/10/2026 21:25", duration_seconds=36)

    assert "Coleta concluída" in html
    assert "36 s" in html
    assert "aviso 1" in html
    assert "aviso 6" not in html, "só os cinco primeiros avisos são listados"
    assert "e mais 1 aviso" in html


def test_dashboard_marks_estimated_freight() -> None:
    html = _render(freight_estimated_orders=3)

    assert "3 pedido(s) com frete" in html
