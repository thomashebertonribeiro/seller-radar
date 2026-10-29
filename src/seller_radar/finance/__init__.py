"""Motor financeiro determinístico."""

from seller_radar.finance.calculations import (
    BreakEvenResult,
    ConversionResult,
    MarginResult,
    MinimumPriceResult,
    SaleLine,
    allocate,
    break_even_revenue,
    contribution_margin,
    conversion,
    gross_revenue,
    marketplace_net,
    minimum_price,
)
from seller_radar.finance.money import D, as_percent, q2, q2_ceil
from seller_radar.finance.quality import Quality, State

__all__ = [
    "BreakEvenResult",
    "ConversionResult",
    "D",
    "MarginResult",
    "MinimumPriceResult",
    "Quality",
    "SaleLine",
    "State",
    "allocate",
    "as_percent",
    "break_even_revenue",
    "contribution_margin",
    "conversion",
    "gross_revenue",
    "marketplace_net",
    "minimum_price",
    "q2",
    "q2_ceil",
]
