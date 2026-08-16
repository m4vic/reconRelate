import asyncio
from types import SimpleNamespace

from reconrelate.db.db import get_connection, init_db
from reconrelate.db.repositories import GraphRepository
from reconrelate.llm_orchestration.model_budget import ModelBudget
from reconrelate.llm_orchestration.relationship_engine import LLMClient


def _response(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=None,
        _hidden_params={},
    )


def _accepted(score: float = 0.9) -> str:
    return (
        '{"abstain":false,"abstention_reason":null,"pivots":['
        f'{{"id_type":"org","value":"Example Holdings","score":{score},'
        '"reason":"entity evidence"}]}'
    )


ABSTAINED = '{"abstain":true,"abstention_reason":"ambiguous","pivots":[]}'


def _client(telemetry) -> LLMClient:  # noqa: ANN001
    return LLMClient(
        model="strong", fast_model="fast",
        budget=ModelBudget(5, 1_000_000, 5_000, 0), telemetry_sink=telemetry.append,
    )


def test_confident_fast_result_short_circuits_primary(monkeypatch) -> None:
    models = []
    monkeypatch.setattr(
        "reconrelate.llm_orchestration.relationship_engine.litellm.completion",
        lambda **kwargs: (models.append(kwargs["model"]) or _response(_accepted())),
    )
    telemetry = []
    client = _client(telemetry)
    result = asyncio.run(client.call_unified("example.com", {"domain": "example.com"}))
    assert result[0].value == "Example Holdings"
    assert models == ["ollama/fast"]
    assert telemetry[0].task == "relationship_pivot_fast"


def test_fast_abstention_escalates_to_primary_and_accounts_both_calls(monkeypatch) -> None:
    def completion(**kwargs):  # noqa: ANN003
        return _response(ABSTAINED if kwargs["model"] == "ollama/fast" else _accepted())

    monkeypatch.setattr(
        "reconrelate.llm_orchestration.relationship_engine.litellm.completion", completion
    )
    telemetry = []
    client = _client(telemetry)
    result = asyncio.run(client.call_unified("example.com", {"domain": "example.com"}))
    assert result and client.sdk_calls == 2
    assert client.budget.calls_reserved == 2
    assert [call.task for call in telemetry] == [
        "relationship_pivot_fast", "relationship_pivot_strong"
    ]


def test_low_confidence_fast_output_is_discarded_when_primary_fails(monkeypatch) -> None:
    def completion(**kwargs):  # noqa: ANN003
        if kwargs["model"] == "ollama/fast":
            return _response(_accepted(0.6))
        raise TimeoutError("primary unavailable")

    monkeypatch.setattr(
        "reconrelate.llm_orchestration.relationship_engine.litellm.completion", completion
    )
    telemetry = []
    client = _client(telemetry)
    assert asyncio.run(client.call_unified("example.com", {"domain": "example.com"})) == []
    assert client.sdk_calls == 2


def test_fast_budget_rejection_does_not_attempt_primary(monkeypatch) -> None:
    called = False

    def completion(**kwargs):  # noqa: ANN003
        nonlocal called
        called = True
        return _response(_accepted())

    monkeypatch.setattr(
        "reconrelate.llm_orchestration.relationship_engine.litellm.completion", completion
    )
    telemetry = []
    client = LLMClient(
        model="strong", fast_model="fast", budget=ModelBudget(0, 0, 0, 0),
        telemetry_sink=telemetry.append,
    )
    assert asyncio.run(client.call_unified("example.com", {"domain": "example.com"})) == []
    assert called is False and len(telemetry) == 1
    assert telemetry[0].task == "relationship_pivot_fast"
    assert telemetry[0].status == "budget_exceeded"


def test_fast_and_strong_cache_keys_replay_independently(monkeypatch) -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    repo = GraphRepository(conn)
    run_id = repo.create_run("example.com", 0, 1, max_model_calls=2)

    def completion(**kwargs):  # noqa: ANN003
        return _response(ABSTAINED if kwargs["model"] == "ollama/fast" else _accepted())

    monkeypatch.setattr(
        "reconrelate.llm_orchestration.relationship_engine.litellm.completion", completion
    )
    client = LLMClient(
        model="strong", fast_model="fast", budget=ModelBudget(2, 1_000_000, 2_000, 0),
        telemetry_sink=repo.record_model_call, durable_budget_sink=repo.reserve_model_budget,
        model_cache_lookup=repo.get_cached_model_result,
    )
    metadata = {"run_id": run_id}
    first = asyncio.run(client.call_unified("example.com", {"domain": "example.com"}, metadata))
    second = asyncio.run(client.call_unified("example.com", {"domain": "example.com"}, metadata))
    assert first == second and client.sdk_calls == 2
    assert repo.get_model_budget_usage(run_id)["calls"] == 2


def test_run_snapshot_persists_both_models_and_routing_policy() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    repo = GraphRepository(conn)
    run_id = repo.create_run(
        "example.com", 0, 1, llm_model="ollama/strong", fast_model="ollama/fast",
        model_routing_policy="economical-first-v1",
    )
    run = repo.get_run_graph(run_id)["run"]
    assert run["llm_model"] == "ollama/strong"
    assert run["fast_model"] == "ollama/fast"
    assert run["model_routing_policy"] == "economical-first-v1"
