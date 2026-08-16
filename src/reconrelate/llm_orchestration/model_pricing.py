"""Dated cloud price envelopes used only for conservative pre-call admission."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_CEILING

from reconrelate.core.errors import ModelPricingUnavailableError

PRICE_CATALOG_VERSION = "openai-2026.08.14-v1"
PRICE_CATALOG_VERIFIED_ON = date(2026, 8, 14)
PRICE_CATALOG_MAX_AGE_DAYS = 90
PRICE_SOURCE_URLS = (
    "https://developers.openai.com/api/docs/models/gpt-5-mini",
    "https://developers.openai.com/api/docs/models/gpt-5.6-luna",
)


@dataclass(frozen=True, slots=True)
class TextTokenPrice:
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal


_PRICES = {
    "gpt-5-mini": TextTokenPrice(Decimal("0.25"), Decimal("2.00")),
    "gpt-5-mini-2025-08-07": TextTokenPrice(Decimal("0.25"), Decimal("2.00")),
    "gpt-5.6-luna": TextTokenPrice(Decimal("0.20"), Decimal("1.20")),
}


def catalog_is_fresh(*, today: date | None = None) -> bool:
    current = today or date.today()
    age = (current - PRICE_CATALOG_VERIFIED_ON).days
    return 0 <= age <= PRICE_CATALOG_MAX_AGE_DAYS


def estimate_cloud_cost_microusd(
    model: str, input_token_upper_bound: int, output_token_ceiling: int, *, today: date | None = None
) -> int:
    """Round a catalog-priced worst-case envelope upward to integer microdollars."""
    if not catalog_is_fresh(today=today):
        raise ModelPricingUnavailableError(
            f"cloud price catalog {PRICE_CATALOG_VERSION} is stale; update ReconRelate before spending"
        )
    price = _PRICES.get(model)
    if price is None:
        raise ModelPricingUnavailableError(
            f"no verified cloud price envelope for model {model!r} in {PRICE_CATALOG_VERSION}"
        )
    # At per-million-token rates, tokens * rate equals microdollars directly.
    value = (
        Decimal(max(0, input_token_upper_bound)) * price.input_usd_per_million
        + Decimal(max(0, output_token_ceiling)) * price.output_usd_per_million
    )
    return int(value.to_integral_value(rounding=ROUND_CEILING))
