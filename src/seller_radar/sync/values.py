"""Conversão dos valores da API para os tipos do banco.

A API do Mercado Livre entrega dinheiro como número JSON (float em Python). O
projeto proíbe `float` em dinheiro (D8), então a conversão acontece **aqui**, na
fronteira, via `str()` — o único ponto do sistema onde isso é permitido — e sai
como `Decimal` quantizado. Nenhum float atravessa para o cálculo.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from seller_radar.finance.money import q2

UNIT_STEP = Decimal("0.0001")


class BadValue(ValueError):
    """Valor que a API mandou em formato inesperado."""


def money(value: Any, *, field: str = "valor") -> Decimal | None:
    """Converte um número JSON em `Decimal` com 2 casas. `None` continua `None`.

    `None` e zero são coisas diferentes: frete desconhecido não é frete grátis.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise BadValue(f"{field}: booleano não é dinheiro")
    if isinstance(value, Decimal):
        return q2(value)
    if isinstance(value, int):
        return q2(Decimal(value))
    if isinstance(value, (float, str)):
        try:
            return q2(Decimal(str(value)))
        except InvalidOperation as exc:
            raise BadValue(f"{field}: {value!r} não é número") from exc
    raise BadValue(f"{field}: tipo inesperado {type(value).__name__}")


def unit_cost(value: Any, *, field: str = "custo unitário") -> Decimal | None:
    """Custo por unidade: 4 casas, porque dividir caixa por unidade gera fração."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise BadValue(f"{field}: booleano não é dinheiro")
    if isinstance(value, (int, float, str, Decimal)):
        try:
            return Decimal(str(value)).quantize(UNIT_STEP)
        except InvalidOperation as exc:
            raise BadValue(f"{field}: {value!r} não é número") from exc
    raise BadValue(f"{field}: tipo inesperado {type(value).__name__}")


def as_int(value: Any, *, field: str = "quantidade") -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise BadValue(f"{field}: booleano não é inteiro")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise BadValue(f"{field}: {value!r} não é inteiro") from exc


def moment(value: Any, *, field: str = "data") -> datetime | None:
    """ISO 8601 da API para `datetime` com fuso, sempre em UTC."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise BadValue(f"{field}: esperava texto ISO 8601")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BadValue(f"{field}: {value!r} não é ISO 8601") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def day(value: Any, *, field: str = "dia") -> date | None:
    parsed = moment(value, field=field)
    return None if parsed is None else parsed.date()


def numbers_as_text(value: Any) -> Any:
    """Normaliza JSON diagnóstico para não carregar float.

    `cost_components` traz razões e descontos que não entram em cálculo, mas o
    projeto não guarda dinheiro como float. Números viram texto decimal exato.
    """
    if isinstance(value, dict):
        return {str(k): numbers_as_text(v) for k, v in value.items()}
    if isinstance(value, list):
        return [numbers_as_text(v) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float, Decimal)):
        return str(Decimal(str(value)).normalize())
    return str(value)
