from datetime import date

import pytest

from reconrelate.core.errors import ModelBudgetExceededError, ModelPricingUnavailableError
from reconrelate.llm_orchestration.model_budget import ModelBudget
from reconrelate.llm_orchestration import model_pricing
from reconrelate.llm_orchestration.model_pricing import (
    PRICE_CATALOG_VERSION,
    estimate_cloud_cost_microusd,
)


@pytest.fixture(autouse=True)
def _clear_runtime_prices():
    model_pricing._RUNTIME_PRICES.clear()
    yield
    model_pricing._RUNTIME_PRICES.clear()


def test_official_rate_envelope_rounds_up_to_microdollars() -> None:
    assert estimate_cloud_cost_microusd(
        "gpt-5-mini", 3, 1, today=date(2026, 8, 14)
    ) == 3
    assert estimate_cloud_cost_microusd(
        "gpt-5.6-luna", 3, 1, today=date(2026, 8, 14)
    ) == 2


def test_unknown_or_stale_price_fails_closed() -> None:
    with pytest.raises(ModelPricingUnavailableError, match="no verified"):
        estimate_cloud_cost_microusd("gpt-unknown", 1, 1, today=date(2026, 8, 14))
    with pytest.raises(ModelPricingUnavailableError, match="stale"):
        estimate_cloud_cost_microusd("gpt-5-mini", 1, 1, today=date(2026, 11, 13))


def test_cloud_cost_ceiling_rejects_before_mutating_budget() -> None:
    budget = ModelBudget(1, 100, 100, 200, 2)
    with pytest.raises(ModelBudgetExceededError, match="cloud cost ceiling"):
        budget.reserve(input_text="abc", output_tokens=1, cloud=True, model="gpt-5-mini")
    assert budget.calls_reserved == 0
    assert budget.cloud_cost_microusd_reserved == 0
    assert PRICE_CATALOG_VERSION.startswith("openai-")


def test_registered_price_makes_a_non_catalog_model_usable() -> None:
    assert not model_pricing.has_price("anthropic/claude-3-5-sonnet")
    model_pricing.register_price(
        "anthropic/claude-3-5-sonnet", 3.0, 15.0, verified_on=date(2026, 8, 20)
    )
    assert model_pricing.has_price("anthropic/claude-3-5-sonnet")
    cost = estimate_cloud_cost_microusd(
        "anthropic/claude-3-5-sonnet", 1_000_000, 1_000_000, today=date(2026, 8, 24)
    )
    assert cost == 18_000_000  # (3.0 + 15.0) USD -> microdollars


def test_registered_price_expires_independently_of_the_built_in_catalog() -> None:
    model_pricing.register_price("custom/model", 1.0, 1.0, verified_on=date(2026, 1, 1))
    with pytest.raises(ModelPricingUnavailableError, match="stale"):
        estimate_cloud_cost_microusd("custom/model", 1, 1, today=date(2026, 8, 24))


def test_registered_price_defaults_verified_on_to_today() -> None:
    model_pricing.register_price("custom/model", 1.0, 1.0)
    # Should not raise: freshly registered with no explicit date defaults to "today".
    estimate_cloud_cost_microusd("custom/model", 1, 1)
