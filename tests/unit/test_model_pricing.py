from datetime import date

import pytest

from reconrelate.core.errors import ModelBudgetExceededError, ModelPricingUnavailableError
from reconrelate.llm_orchestration.model_budget import ModelBudget
from reconrelate.llm_orchestration.model_pricing import (
    PRICE_CATALOG_VERSION,
    estimate_cloud_cost_microusd,
)


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
