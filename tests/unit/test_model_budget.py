import asyncio

import pytest
from types import SimpleNamespace

from reconrelate.core.errors import ModelBudgetExceededError
from reconrelate.llm_orchestration.model_budget import ModelBudget
from reconrelate.llm_orchestration.relationship_engine import LLMClient


def test_call_ceiling_rejects_before_reserving_more_tokens() -> None:
    budget = ModelBudget(1, 100, 20, 0)
    budget.reserve(input_text="first", output_tokens=10, cloud=False)

    with pytest.raises(ModelBudgetExceededError, match="call ceiling"):
        budget.reserve(input_text="second", output_tokens=10, cloud=False)

    assert budget.snapshot() == {
        "calls_reserved": 1,
        "input_tokens_reserved": 5,
        "output_tokens_reserved": 10,
        "cloud_tokens_reserved": 0,
        "cloud_cost_microusd_reserved": 0,
    }


def test_cloud_budget_reserves_input_and_output_before_call() -> None:
    budget = ModelBudget(2, 100, 20, 14, 100)
    budget.reserve(input_text="abcd", output_tokens=10, cloud=True, model="gpt-5-mini")

    assert budget.cloud_tokens_reserved == 14
    with pytest.raises(ModelBudgetExceededError, match="cloud token ceiling"):
        budget.reserve(input_text="x", output_tokens=1, cloud=True, model="gpt-5-mini")
    assert budget.calls_reserved == 1


def test_llm_client_budget_rejection_never_reaches_litellm(monkeypatch) -> None:
    called = False

    def forbidden_completion(**kwargs):  # noqa: ANN003
        nonlocal called
        called = True
        raise AssertionError("SDK must not be reached")

    monkeypatch.setattr("reconrelate.llm_orchestration.relationship_engine.litellm.completion", forbidden_completion)
    telemetry = []
    client = LLMClient(
        model="gpt-5", budget=ModelBudget(1, 100_000, 1_000, 0),
        telemetry_sink=telemetry.append,
    )

    assert asyncio.run(client.call_unified("example.com", {"domain": "example.com"})) == []
    assert called is False
    assert client.budget.calls_reserved == 0
    assert telemetry[0].status == "budget_exceeded"
    assert telemetry[0].actual_total_tokens is None
    assert telemetry[0].reserved_cloud_tokens == 0


def test_success_records_provider_reported_usage_and_cost(monkeypatch) -> None:
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=(
            '{"abstain":true,"abstention_reason":"insufficient evidence","pivots":[]}'
        )))],
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=3, total_tokens=15),
        _hidden_params={"response_cost": 0.0007},
    )
    monkeypatch.setattr(
        "reconrelate.llm_orchestration.relationship_engine.litellm.completion",
        lambda **kwargs: response,
    )
    telemetry = []
    client = LLMClient(
        model="gpt-5-mini", budget=ModelBudget(1, 100_000, 1_000, 100_000, 100_000),
        telemetry_sink=telemetry.append,
    )

    assert asyncio.run(client.call_unified(
        "example.com", {"domain": "example.com"}, {"run_id": "run-1"}
    )) == []
    call = telemetry[0]
    assert call.run_id == "run-1"
    assert call.status == "success"
    assert call.actual_input_tokens == 12
    assert call.actual_output_tokens == 3
    assert call.actual_total_tokens == 15
    assert call.provider_reported_cost_usd == 0.0007
    assert call.reserved_cloud_tokens > 512
    assert call.reserved_cloud_cost_microusd > 0
