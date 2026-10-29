"""Os exemplos numéricos de docs/formulas.md como teste executável.

Se um número do documento mudar sem o código mudar (ou o contrário), esta suíte
quebra. É o que impede a documentação de virar ficção.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from seller_radar.finance import (
    D,
    Quality,
    SaleLine,
    State,
    allocate,
    as_percent,
    break_even_revenue,
    contribution_margin,
    conversion,
    gross_revenue,
    marketplace_net,
    minimum_price,
    q2,
)


def line(**overrides: object) -> SaleLine:
    base: dict[str, object] = {
        "unit_price": D("100.00"),
        "quantity": 2,
        "seller_discount": D("10.00"),
        "commission_pct": D("0.12"),
        "shipping_paid_by_seller": D("15.00"),
        "tax_rate": D("0.06"),
        "unit_cost": D("40.00"),
        "other_variable": D("2.00"),
        "ad_cost": D("5.00"),
    }
    base.update(overrides)
    return SaleLine(**base)  # type: ignore[arg-type]


# --- §1 e §2 ---------------------------------------------------------------


def test_gross_revenue_matches_doc_example() -> None:
    assert gross_revenue(line()) == D("190.00")


def test_marketplace_net_matches_doc_example() -> None:
    assert marketplace_net(line()) == D("152.20")


def test_fixed_commission_is_absolute_not_percentage() -> None:
    com_fixa = line(commission_fixed_per_unit=D("6.00"))

    # 190 - 22,80 - 12,00 (fixa × 2) - 15,00
    assert marketplace_net(com_fixa) == D("140.20")


# --- §3 --------------------------------------------------------------------


def test_contribution_margin_matches_doc_example() -> None:
    result = contribution_margin(line())

    assert result.contribution_margin == D("53.80")
    assert as_percent(result.margin_pct) == Decimal("28.3158")
    assert result.quality.is_complete


def test_missing_cost_yields_none_not_zero() -> None:
    result = contribution_margin(line(unit_cost=None))

    assert result.contribution_margin is None
    assert result.margin_pct is None
    assert result.quality.state is State.INCOMPLETE
    assert "custo" in result.quality.reasons


def test_missing_advertising_is_reported_but_margin_is_still_computed() -> None:
    result = contribution_margin(line(ad_cost=None))

    assert result.contribution_margin == D("58.80")  # 53,80 + 5,00
    assert result.quality.state is State.INCOMPLETE
    assert "publicidade" in result.quality.reasons


def test_estimated_freight_is_not_dressed_as_complete() -> None:
    result = contribution_margin(line(freight_allocated=True))

    assert result.quality.state is State.ESTIMATED
    assert "frete_rateado" in result.quality.reasons


# --- §4 --------------------------------------------------------------------


def test_break_even_matches_doc_example() -> None:
    result = break_even_revenue(D("5000.00"), [(D("190.00"), D("53.80"))])

    assert result.revenue == D("17657.99")
    assert result.quality.is_complete


def test_break_even_is_weighted_by_revenue_not_simple_average() -> None:
    ponderado = break_even_revenue(
        D("5000.00"), [(D("9000.00"), D("2700.00")), (D("1000.00"), D("200.00"))]
    )

    assert ponderado.revenue == D("17241.38")
    assert ponderado.revenue != D("20000.00")  # média simples daria isto


def test_break_even_without_fixed_costs_explains_why() -> None:
    result = break_even_revenue(D("0"), [(D("190.00"), D("53.80"))])

    assert result.revenue is None
    assert "custos_fixos" in result.quality.reasons


def test_break_even_without_revenue_explains_why() -> None:
    result = break_even_revenue(D("5000.00"), [])

    assert result.revenue is None
    assert "receita" in result.quality.reasons


def test_break_even_never_divides_by_zero() -> None:
    result = break_even_revenue(D("5000.00"), [(D("190.00"), D("0"))])

    assert result.revenue is None
    assert "margem_nao_positiva" in result.quality.reasons


# --- §5 --------------------------------------------------------------------


def test_minimum_price_matches_doc_example() -> None:
    result = minimum_price(
        unit_cost=D("40.00"),
        shipping=D("15.00"),
        other_variable=D("2.00"),
        commission_pct=D("0.12"),
        tax_rate=D("0.06"),
        target_margin=D("0.20"),
    )

    assert result.price == D("91.94")
    assert result.achieved_margin_pct >= D("0.20")


def test_minimum_price_includes_fixed_commission_in_numerator() -> None:
    result = minimum_price(
        unit_cost=D("40.00"),
        shipping=D("15.00"),
        other_variable=D("2.00"),
        commission_pct=D("0.12"),
        commission_fixed=D("6.00"),
        tax_rate=D("0.06"),
        target_margin=D("0.20"),
    )

    assert result.price == D("101.62")
    assert result.achieved_margin_pct >= D("0.20")


def test_minimum_price_rounds_up_never_down() -> None:
    params = dict(
        unit_cost=D("40.00"),
        shipping=D("15.00"),
        other_variable=D("2.00"),
        commission_pct=D("0.12"),
        tax_rate=D("0.06"),
        target_margin=D("0.20"),
    )
    result = minimum_price(**params)  # type: ignore[arg-type]
    exact = D("57.00") / D("0.62")

    assert result.price is not None
    assert result.price >= exact, "arredondar para baixo entregaria preço abaixo da meta"
    assert result.price == q2(exact)  # aqui 91,9355 → 91,94 por HALF_UP coincide


def test_impossible_target_is_reported_not_guessed() -> None:
    result = minimum_price(
        unit_cost=D("40.00"),
        shipping=D("15.00"),
        commission_pct=D("0.50"),
        tax_rate=D("0.30"),
        target_margin=D("0.30"),
    )

    assert result.price is None
    assert "alvo_inatingivel" in result.quality.reasons


# --- §6 --------------------------------------------------------------------


def test_conversion_without_visits_is_undefined() -> None:
    result = conversion(sales=10, visits=0)

    assert result.rate is None
    assert "visitas" in result.quality.reasons


def test_conversion_rate() -> None:
    assert conversion(sales=5, visits=200).rate == D("0.025")


# --- §8 --------------------------------------------------------------------


def test_money_rejects_float() -> None:
    with pytest.raises(TypeError):
        D(0.1)  # type: ignore[arg-type]


def test_rounding_is_half_up() -> None:
    assert q2(Decimal("2.675")) == Decimal("2.68")
    assert q2(Decimal("2.665")) == Decimal("2.67")


def test_allocate_closes_exactly_on_total() -> None:
    parts, quality = allocate(D("100.00"), [D("1"), D("1"), D("1")])

    assert sum(parts) == D("100.00")
    assert parts == [D("33.33"), D("33.33"), D("33.34")]
    assert quality.state is State.ESTIMATED


def test_quality_severity_merge() -> None:
    merged = Quality.estimated("rateio").merge(Quality.incomplete("custo"))

    assert merged.state is State.INCOMPLETE
    assert merged.reasons == ("custo", "rateio")


def test_invalid_percentage_is_rejected() -> None:
    with pytest.raises(ValueError, match="commission_pct"):
        line(commission_pct=D("1.5"))


def test_zero_quantity_is_rejected() -> None:
    with pytest.raises(ValueError, match="quantidade"):
        line(quantity=0)
