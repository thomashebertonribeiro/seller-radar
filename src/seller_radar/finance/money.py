"""Aritmética monetária: `Decimal` sempre, `float` nunca.

Regras (docs/formulas.md §8):
- intermediários sem quantização;
- valores persistidos/exibidos: 2 casas, `ROUND_HALF_UP`;
- preço mínimo: 2 casas, `ROUND_CEILING` (para não entregar preço abaixo da meta);
- percentual exibido: 4 casas sobre o valor em pontos percentuais.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
PERCENT_STEP = Decimal("0.0001")


def D(value: str | int | Decimal) -> Decimal:
    """Converte para `Decimal` recusando `float`.

    Aceitar `float` reintroduziria o erro binário que o projeto evita. Se um dia
    for mesmo necessário, a conversão precisa ser explícita na origem, via `str`.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise TypeError("bool não é valor monetário")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        return Decimal(value)
    if isinstance(value, float):
        raise TypeError("float proibido em dinheiro; use str ou Decimal")
    raise TypeError(f"tipo não suportado para dinheiro: {type(value).__name__}")


def q2(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def q2_ceil(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_CEILING)


def as_percent(fraction: Decimal) -> Decimal:
    """Fração (0,283157…) para pontos percentuais exibíveis (28,3158)."""
    return (fraction * 100).quantize(PERCENT_STEP, rounding=ROUND_HALF_UP)
