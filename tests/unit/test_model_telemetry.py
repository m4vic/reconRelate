from reconrelate.db.db import get_connection, init_db
from reconrelate.db.repositories import GraphRepository
from reconrelate.llm_orchestration.model_telemetry import ModelCallTelemetry
from reconrelate.output.renderers import render_markdown_report


def test_model_telemetry_roundtrips_without_inventing_missing_actual_usage() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    repo = GraphRepository(conn)
    run_id = repo.create_run("example.com", 0, 1)
    repo.record_model_call(ModelCallTelemetry(
        run_id=run_id, domain="example.com", model="ollama/test",
        task="relationship_pivot", policy_version="relationship-pivot-v1",
        cloud=False, status="success", reserved_input_tokens=100,
        reserved_output_tokens=512, reserved_cloud_tokens=0,
        actual_input_tokens=None, actual_output_tokens=None, actual_total_tokens=None,
        provider_reported_cost_usd=None, latency_ms=8, error_class=None,
        error_message=None, started_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T00:00:00+00:00",
    ))

    graph = repo.get_run_graph(run_id)
    usage = graph["model_usage"][0]
    assert usage["calls"] == 1
    assert usage["reserved_input_tokens"] == 100
    assert usage["actual_total_tokens"] is None
    assert usage["provider_reported_cost_usd"] is None
    assert usage["output_disposition"] is None
    report = render_markdown_report(graph)
    assert "## Model Usage" in report
    assert "actual total `unknown`" in report
    assert "provider-reported cost `unknown`" in report
    assert "output `none`, egress `legacy`" in report


def test_budget_rejection_persists_zero_actual_and_reserved_usage() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    repo = GraphRepository(conn)
    run_id = repo.create_run("example.com", 0, 1)
    repo.record_model_call(ModelCallTelemetry(
        run_id=run_id, domain="example.com", model="gpt-5",
        task="relationship_pivot", policy_version="relationship-pivot-v1",
        cloud=True, status="budget_exceeded", reserved_input_tokens=0,
        reserved_output_tokens=0, reserved_cloud_tokens=0,
        actual_input_tokens=None, actual_output_tokens=None, actual_total_tokens=None,
        provider_reported_cost_usd=None, latency_ms=0,
        error_class="ModelBudgetExceededError", error_message="ceiling",
        started_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T00:00:00+00:00",
    ))

    usage = repo.get_model_usage(run_id)[0]
    assert usage["status"] == "budget_exceeded"
    assert usage["reserved_cloud_tokens"] == 0
    assert usage["actual_total_tokens"] is None
