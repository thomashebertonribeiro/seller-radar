"""Motor financeiro determinístico — fórmulas de docs/formulas.md.

Nenhuma função aqui arredonda intermediários. O arredondamento acontece na borda
(persistência/exibição), com `q2`/`q2_ceil`, ou explicitamente onde a regra manda
(preço mínimo para cima).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from seller_radar.finance.money import D, q2, q2_ceil
from seller_radar.finance.quality import Quality

ZERO = Decimal("0")


@dataclass(frozen=True)
class SaleLine:
    """Uma linha de venda já normalizada a partir do pedido."""

    unit_price: Decimal
    quantity: int
    seller_discount: Decimal = ZERO
    commission_pct: Decimal = ZERO
    commission_fixed_per_unit: Decimal = ZERO
    shipping_paid_by_seller: Decimal = ZERO
    tax_rate: Decimal = ZERO
    unit_cost: Decimal | None = None
    other_variable: Decimal = ZERO
    ad_cost: Decimal | None = None
    freight_allocated: bool = False

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("quantidade precisa ser positiva")
        if self.unit_price < 0:
            raise ValueError("preço unitário não pode ser negativo")
        for nome, taxa in (("commission_pct", self.commission_pct), ("tax_rate", self.tax_rate)):
            if not (0 <= taxa < 1):
                raise ValueError(f"{nome} precisa estar entre 0 e 1")
        for nome, valor in (
            ("seller_discount", self.seller_discount),
            ("commission_fixed_per_unit", self.commission_fixed_per_unit),
            ("shipping_paid_by_seller", self.shipping_paid_by_seller),
            ("other_variable", self.other_variable),
        ):
            if valor < 0:
                raise ValueError(f"{nome} não pode ser negativo")


@dataclass(frozen=True)
class MarginResult:
    gross_revenue: Decimal
    marketplace_net: Decimal
    taxes: Decimal
    product_cost: Decimal | None
    other_variables: Decimal
    advertising: Decimal | None
    contribution_margin: Decimal | None
    margin_pct: Decimal | None
    quality: Quality


@dataclass(frozen=True)
class BreakEvenResult:
    revenue: Decimal | None
    weighted_margin_pct: Decimal | None
    quality: Quality


@dataclass(frozen=True)
class MinimumPriceResult:
    price: Decimal | None
    achieved_margin_pct: Decimal | None
    quality: Quality


@dataclass(frozen=True)
class ConversionResult:
    rate: Decimal | None
    quality: Quality = field(default_factory=Quality.complete)


def gross_revenue(line: SaleLine) -> Decimal:
    """§1 — desconto do vendedor reduz; subsídio do ML não entra aqui."""
    return line.unit_price * line.quantity - line.seller_discount


def marketplace_net(line: SaleLine) -> Decimal:
    """§2 — receita bruta menos tarifa percentual, tarifa fixa e frete do vendedor."""
    gross = gross_revenue(line)
    commission = gross * line.commission_pct + line.commission_fixed_per_unit * line.quantity
    return gross - commission - line.shipping_paid_by_seller


def contribution_margin(line: SaleLine) -> MarginResult:
    """§3 — margem de contribuição, com o motivo de cada ausência.

    Sem custo cadastrado a margem é `None`, não zero: um número sem custo
    superestima o resultado e é pior que a ausência do número.
    """
    gross = gross_revenue(line)
    net = marketplace_net(line)
    taxes = gross * line.tax_rate

    quality = Quality.complete()
    if line.unit_cost is None:
        quality = quality.merge(Quality.incomplete("custo"))
    if line.ad_cost is None:
        quality = quality.merge(Quality.incomplete("publicidade"))
    if line.freight_allocated:
        quality = quality.merge(Quality.estimated("frete_rateado"))

    ad = ZERO if line.ad_cost is None else line.ad_cost
    cost = None if line.unit_cost is None else line.unit_cost * line.quantity

    margin: Decimal | None
    pct: Decimal | None
    if cost is None:
        margin, pct = None, None
    else:
        margin = net - taxes - cost - line.other_variable - ad
        pct = None if gross == 0 else margin / gross

    return MarginResult(
        gross_revenue=gross,
        marketplace_net=net,
        taxes=taxes,
        product_cost=cost,
        other_variables=line.other_variable,
        advertising=None if line.ad_cost is None else ad,
        contribution_margin=margin,
        margin_pct=pct,
        quality=quality,
    )


def break_even_revenue(
    fixed_costs: Decimal, entries: Iterable[tuple[Decimal, Decimal]]
) -> BreakEvenResult:
    """§4 — custos fixos ÷ margem ponderada **por receita**.

    `entries` são pares (receita, margem de contribuição). Média simples entre
    anúncios é descartada: dá o mesmo peso a quem vende R$ 50 e a quem vende
    R$ 9.000.
    """
    fixed = D(fixed_costs)
    total_revenue = ZERO
    total_margin = ZERO
    for revenue, margin in entries:
        total_revenue += D(revenue)
        total_margin += D(margin)

    if fixed <= 0:
        return BreakEvenResult(None, None, Quality.incomplete("custos_fixos"))
    if total_revenue <= 0:
        return BreakEvenResult(None, None, Quality.incomplete("receita"))

    weighted = total_margin / total_revenue
    if weighted <= 0:
        return BreakEvenResult(None, None, Quality.incomplete("margem_nao_positiva"))

    return BreakEvenResult(q2(fixed / weighted), weighted, Quality.complete())


def minimum_price(
    *,
    unit_cost: Decimal,
    shipping: Decimal,
    commission_pct: Decimal,
    tax_rate: Decimal,
    target_margin: Decimal,
    other_variable: Decimal = ZERO,
    commission_fixed: Decimal = ZERO,
) -> MinimumPriceResult:
    """§5 — preço mínimo com tarifa fixa no numerador e arredondamento para cima."""
    denominator = D("1") - commission_pct - tax_rate - target_margin
    if denominator <= 0:
        return MinimumPriceResult(None, None, Quality.incomplete("alvo_inatingivel"))

    numerator = D(unit_cost) + D(shipping) + D(other_variable) + D(commission_fixed)
    price = q2_ceil(numerator / denominator)
    return MinimumPriceResult(price, _achieved_margin_pct(price, unit_cost, shipping,
                                                          commission_pct, commission_fixed,
                                                          tax_rate, other_variable),
                              Quality.complete())


def _achieved_margin_pct(
    price: Decimal,
    unit_cost: Decimal,
    shipping: Decimal,
    commission_pct: Decimal,
    commission_fixed: Decimal,
    tax_rate: Decimal,
    other_variable: Decimal,
) -> Decimal:
    commission = price * commission_pct + commission_fixed
    net = price - commission - shipping
    tax = price * tax_rate
    margin = net - tax - D(unit_cost) - D(other_variable)
    return margin / price


def conversion(sales: int, visits: int) -> ConversionResult:
    """§6 — zero visitas é indeterminado, não 0%."""
    if sales < 0 or visits < 0:
        raise ValueError("contagens não podem ser negativas")
    if visits == 0:
        return ConversionResult(None, Quality.incomplete("visitas"))
    return ConversionResult(D(sales) / D(visits))


def allocate(total: Decimal, weights: Sequence[Decimal]) -> tuple[list[Decimal], Quality]:
    """Rateio proporcional que fecha exatamente no total.

    A sobra de arredondamento vai para a última parcela, em vez de se perder ou
    de criar centavos do nada.
    """
    if not weights:
        raise ValueError("rateio sem parcelas")
    total_weight = sum(weights, ZERO)
    if total_weight <= 0:
        raise ValueError("soma dos pesos precisa ser positiva")

    parts = [q2(D(total) * w / total_weight) for w in weights]
    residual = q2(D(total)) - sum(parts, ZERO)
    parts[-1] = q2(parts[-1] + residual)
    return parts, Quality.estimated("rateio")
