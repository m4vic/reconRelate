from pathlib import Path

import pytest

from reconrelate.core.errors import ModelBudgetExceededError
from reconrelate.db.db import get_connection, init_db
from reconrelate.db.repositories import GraphRepository
from reconrelate.llm_orchestration.model_budget import ModelReservation


def _repos(path: Path) -> tuple[GraphRepository, GraphRepository]:
    first_conn = get_connection(str(path))
    init_db(first_conn)
    second_conn = get_connection(str(path))
    init_db(second_conn)
    return GraphRepository(first_conn), GraphRepository(second_conn)


def test_two_connections_share_one_atomic_model_call_ceiling(tmp_path: Path) -> None:
    first, second = _repos(tmp_path / "model-budget.sqlite")
    run_id = first.create_run(
        "example.com", 0, 1, max_model_calls=1,
        max_model_input_tokens=100, max_model_output_tokens=20, max_cloud_tokens=120,
    )
    reservation = ModelReservation(input_tokens=10, output_tokens=5, cloud_tokens=15)

    first.reserve_model_budget(run_id, "gpt-test", "example.com", reservation)
    with pytest.raises(ModelBudgetExceededError, match="model call ceiling"):
        second.reserve_model_budget(run_id, "gpt-test", "example.net", reservation)

    assert second.get_model_budget_usage(run_id) == {
        "calls": 1, "input_tokens": 10, "output_tokens": 5, "cloud_tokens": 15,
        "cloud_cost_microusd": 0,
    }


def test_crash_without_telemetry_keeps_conservative_reservation(tmp_path: Path) -> None:
    first, resumed = _repos(tmp_path / "model-resume.sqlite")
    run_id = first.create_run(
        "example.com", 0, 1, max_model_calls=2,
        max_model_input_tokens=10, max_model_output_tokens=10, max_cloud_tokens=20,
    )
    first.reserve_model_budget(
        run_id, "gpt-test", "example.com",
        ModelReservation(input_tokens=8, output_tokens=2, cloud_tokens=10),
    )

    with pytest.raises(ModelBudgetExceededError, match="input-token ceiling"):
        resumed.reserve_model_budget(
            run_id, "gpt-test", "example.com",
            ModelReservation(input_tokens=3, output_tokens=1, cloud_tokens=4),
        )
    assert resumed.get_model_usage(run_id) == []
    assert resumed.get_model_budget_usage(run_id)["calls"] == 1


def test_rejected_reservation_is_transactionally_absent(tmp_path: Path) -> None:
    repo, _ = _repos(tmp_path / "model-rollback.sqlite")
    run_id = repo.create_run(
        "example.com", 0, 1, max_model_calls=1,
        max_model_input_tokens=1, max_model_output_tokens=1, max_cloud_tokens=1,
    )

    with pytest.raises(ModelBudgetExceededError):
        repo.reserve_model_budget(
            run_id, "gpt-test", "example.com",
            ModelReservation(input_tokens=2, output_tokens=0, cloud_tokens=0),
        )
    assert repo.get_model_budget_usage(run_id)["calls"] == 0


def test_two_connections_share_atomic_cloud_cost_ceiling(tmp_path: Path) -> None:
    first, second = _repos(tmp_path / "model-cost.sqlite")
    run_id = first.create_run(
        "example.com", 0, 1, max_model_calls=2, max_model_input_tokens=100,
        max_model_output_tokens=100, max_cloud_tokens=200, max_cloud_cost_microusd=5,
    )
    first.reserve_model_budget(
        run_id, "gpt-5-mini", "example.com", ModelReservation(10, 5, 15, 4)
    )
    with pytest.raises(ModelBudgetExceededError, match="cost-microdollar"):
        second.reserve_model_budget(
            run_id, "gpt-5-mini", "example.net", ModelReservation(10, 5, 15, 2)
        )
    assert second.get_model_budget_usage(run_id)["cloud_cost_microusd"] == 4
