import asyncio
from types import SimpleNamespace

from reconrelate.db.db import get_connection, init_db
from reconrelate.db.repositories import GraphRepository
from reconrelate.llm_orchestration.model_budget import ModelBudget
from reconrelate.llm_orchestration.relationship_engine import LLMClient


def _response() -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=(
            '{"abstain":false,"abstention_reason":null,"pivots":['
            '{"id_type":"email","value":"owner@example.com",'
            '"score":0.9,"reason":"registrant"}]}'
        )))],
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=8, total_tokens=20),
        _hidden_params={},
    )


def _client(repo: GraphRepository) -> LLMClient:
    return LLMClient(
        model="ollama/test", budget=ModelBudget(10, 1_000_000, 10_000, 0),
        telemetry_sink=repo.record_model_call,
        durable_budget_sink=repo.reserve_model_budget,
        model_cache_lookup=repo.get_cached_model_result,
    )


def test_successful_normalized_result_replays_without_second_sdk_call(monkeypatch) -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    repo = GraphRepository(conn)
    run_id = repo.create_run("example.com", 0, 1, max_model_calls=2)
    calls = 0

    def completion(**kwargs):  # noqa: ANN003
        nonlocal calls
        calls += 1
        return _response()

    monkeypatch.setattr(
        "reconrelate.llm_orchestration.relationship_engine.litellm.completion", completion
    )
    client = _client(repo)
    metadata = {"run_id": run_id}
    first = asyncio.run(client.call_unified("example.com", {"domain": "example.com"}, metadata))
    second = asyncio.run(client.call_unified("example.com", {"domain": "example.com"}, metadata))

    assert first == second
    assert first[0].value == "owner@example.com"
    assert calls == 1
    assert repo.get_model_budget_usage(run_id)["calls"] == 1
    assert sum(row["calls"] for row in repo.get_model_usage(run_id)) == 1


def test_ambiguous_failed_call_is_not_retried(monkeypatch) -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    repo = GraphRepository(conn)
    run_id = repo.create_run("example.com", 0, 1, max_model_calls=3)
    calls = 0

    def fails(**kwargs):  # noqa: ANN003
        nonlocal calls
        calls += 1
        raise TimeoutError("ambiguous upstream timeout")

    monkeypatch.setattr(
        "reconrelate.llm_orchestration.relationship_engine.litellm.completion", fails
    )
    client = _client(repo)
    metadata = {"run_id": run_id}
    assert asyncio.run(client.call_unified("example.com", {"domain": "example.com"}, metadata)) == []
    assert asyncio.run(client.call_unified("example.com", {"domain": "example.com"}, metadata)) == []

    assert calls == 1
    assert repo.get_model_budget_usage(run_id)["calls"] == 1
    statuses = {row["status"]: row["calls"] for row in repo.get_model_usage(run_id)}
    assert statuses == {"error": 1, "budget_exceeded": 1}


def test_changed_evidence_gets_a_distinct_request_key(monkeypatch) -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    repo = GraphRepository(conn)
    run_id = repo.create_run("example.com", 0, 1, max_model_calls=2)
    calls = 0

    def completion(**kwargs):  # noqa: ANN003
        nonlocal calls
        calls += 1
        return _response()

    monkeypatch.setattr(
        "reconrelate.llm_orchestration.relationship_engine.litellm.completion", completion
    )
    client = _client(repo)
    metadata = {"run_id": run_id}
    asyncio.run(client.call_unified("example.com", {"domain": "one.example.com"}, metadata))
    asyncio.run(client.call_unified("example.com", {"domain": "two.example.com"}, metadata))

    assert calls == 2
    assert repo.get_model_budget_usage(run_id)["calls"] == 2


def test_invalid_output_is_not_cached_and_identical_retry_is_suppressed(monkeypatch) -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    repo = GraphRepository(conn)
    run_id = repo.create_run("example.com", 0, 1, max_model_calls=2)
    calls = 0

    def completion(**kwargs):  # noqa: ANN003
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"pivots":[]}'))],
            usage=None, _hidden_params={},
        )

    monkeypatch.setattr(
        "reconrelate.llm_orchestration.relationship_engine.litellm.completion", completion
    )
    client = _client(repo)
    metadata = {"run_id": run_id}
    assert asyncio.run(client.call_unified("example.com", {"domain": "example.com"}, metadata)) == []
    assert asyncio.run(client.call_unified("example.com", {"domain": "example.com"}, metadata)) == []

    assert calls == 1
    first = conn.execute(
        "SELECT output_disposition, result_json FROM model_calls ORDER BY started_at LIMIT 1"
    ).fetchone()
    assert tuple(first) == ("invalid", None)
    assert repo.get_model_budget_usage(run_id)["calls"] == 1
