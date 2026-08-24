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

# User-supplied price envelopes for models outside the built-in catalog (e.g. a `model add`
# profile for Anthropic or a custom OpenAI-compatible endpoint). Each entry carries its own
# verified-on date rather than sharing PRICE_CATALOG_VERIFIED_ON, since it was never actually
# checked against that catalog. Keyed by the exact litellm model id callers pass in (the same
# string used at both `models doctor` and per-call reservation time), so a profile's price is
# found the same way regardless of when it's looked up.
_RUNTIME_PRICES: dict[str, tuple[TextTokenPrice, date]] = {}


def register_price(
    model: str, input_usd_per_million: float, output_usd_per_million: float, *, verified_on: date | None = None
) -> None:
    """Register a user-supplied price envelope, keyed by the exact litellm model id."""
    _RUNTIME_PRICES[model] = (
        TextTokenPrice(Decimal(str(input_usd_per_million)), Decimal(str(output_usd_per_million))),
        verified_on or date.today(),
    )


def has_price(model: str) -> bool:
    """True if `model` has either a built-in or a registered user-supplied price envelope."""
    return model in _PRICES or model in _RUNTIME_PRICES


def catalog_is_fresh(*, today: date | None = None) -> bool:
    current = today or date.today()
    age = (current - PRICE_CATALOG_VERIFIED_ON).days
    return 0 <= age <= PRICE_CATALOG_MAX_AGE_DAYS


def _runtime_price_is_fresh(verified_on: date, *, today: date | None = None) -> bool:
    current = today or date.today()
    age = (current - verified_on).days
    return 0 <= age <= PRICE_CATALOG_MAX_AGE_DAYS


def estimate_cloud_cost_microusd(
    model: str, input_token_upper_bound: int, output_token_ceiling: int, *, today: date | None = None
) -> int:
    """Round a priced worst-case envelope upward to integer microdollars.

    Checks a user-registered price (from a `model add --input-price/--output-price` profile)
    before the built-in catalog, so any provider becomes usable once a profile supplies a price
    — not only the two OpenAI models ReconRelate ships prices for.
    """
    runtime_entry = _RUNTIME_PRICES.get(model)
    if runtime_entry is not None:
        price, verified_on = runtime_entry
        if not _runtime_price_is_fresh(verified_on, today=today):
            raise ModelPricingUnavailableError(
                f"price envelope for {model!r} was verified on {verified_on.isoformat()} and is "
                f"stale after {PRICE_CATALOG_MAX_AGE_DAYS} days; re-confirm pricing and re-run "
                "`model add` to refresh it before spending"
            )
    else:
        if not catalog_is_fresh(today=today):
            raise ModelPricingUnavailableError(
                f"cloud price catalog {PRICE_CATALOG_VERSION} is stale; update ReconRelate before spending"
            )
        price = _PRICES.get(model)
        if price is None:
            raise ModelPricingUnavailableError(
                f"no verified cloud price envelope for model {model!r} in {PRICE_CATALOG_VERSION}; "
                "run `reconrelate model add` with --input-price and --output-price to supply one"
            )
    # At per-million-token rates, tokens * rate equals microdollars directly.
    value = (
        Decimal(max(0, input_token_upper_bound)) * price.input_usd_per_million
        + Decimal(max(0, output_token_ceiling)) * price.output_usd_per_million
    )
    return int(value.to_integral_value(rounding=ROUND_CEILING))
