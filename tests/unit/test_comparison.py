import json
from pathlib import Path

import pytest

from reconrelate.cli.app import main
from reconrelate.quality.comparison import compare_graphs, render_comparison
from reconrelate.quality.evaluation import EvaluationCase


FIXTURES = Path(__file__).parents[1] / "eval"


def _case() -> EvaluationCase:
    return EvaluationCase.from_path(FIXTURES / "cases" / "example.json")


def _graph(domains: list[str], *, profile: str, calls: int = 0, units: float = 0) -> dict:
    return {
        "run": {
            "id": f"{profile}-run", "root_domain": "example.com", "provider_profile": profile,
            "max_depth": 1, "pivot_top_k": 3,
        },
        "nodes": [
            {"id": str(index), "node_type": "domain", "value_norm": domain}
            for index, domain in enumerate(["example.com", *domains])
        ],
        "provider_usage": [{
            "provider": "source", "capability": "reverse_whois", "status": "success",
            "calls": calls, "attempts": calls, "upstream_requests": calls, "pages": calls,
            "latency_ms": calls * 10, "units": units,
        }] if calls else [],
    }


def test_matched_comparison_reports_labeled_gain_and_usage_delta() -> None:
    baseline = _graph(["example.org"], profile="free", calls=1)
    candidate = _graph(["example.net"], profile="byok", calls=3, units=2)

    result = compare_graphs(baseline, candidate, _case())

    assert result.verdict == "improved_on_labeled_case"
    assert result.new_true_positives == ("example.net",)
    assert result.resolved_known_false_positives == ("example.org",)
    assert result.new_known_false_positives == ()
    assert result.recall_delta == 1.0
    assert result.usage_delta["calls"] == 2
    assert result.usage_delta["billable_units"] == 2
    assert result.incremental_calls_per_new_true_positive == 2
    assert result.incremental_billable_units_per_new_true_positive == 2
    assert result.matched_policy_fields == ("max_depth", "pivot_top_k")
    assert "llm_model" in result.unverified_policy_fields
    assert result.eligible_for_planner_learning is False
    assert any("20 labeled domains" in reason for reason in result.learning_gate_reasons)
    assert "Eligible for planner learning: no" in render_comparison(result)


def test_candidate_only_unlabeled_discovery_is_inconclusive() -> None:
    result = compare_graphs(
        _graph([], profile="free"),
        _graph(["example.invalid"], profile="byok"),
        _case(),
    )

    assert result.verdict == "inconclusive_unlabeled_change"
    assert result.candidate_added_unlabeled == ("example.invalid",)
    assert any("remain unlabeled" in reason for reason in result.learning_gate_reasons)


def test_rejects_different_crawl_policy() -> None:
    baseline = _graph([], profile="free")
    candidate = _graph([], profile="byok")
    candidate["run"]["max_depth"] = 2

    with pytest.raises(ValueError, match="not a matched comparison"):
        compare_graphs(baseline, candidate, _case())


def test_rejects_different_model_policy_when_exported() -> None:
    baseline = _graph([], profile="free")
    candidate = _graph([], profile="byok")
    baseline["run"]["llm_model"] = "model-a"
    candidate["run"]["llm_model"] = "model-b"

    with pytest.raises(ValueError, match="llm_model"):
        compare_graphs(baseline, candidate, _case())


def test_cli_compare_is_offline_and_machine_readable(tmp_path, capsys) -> None:
    baseline_path = tmp_path / "free.graph.json"
    candidate_path = tmp_path / "byok.graph.json"
    baseline_path.write_text(json.dumps(_graph([], profile="free")), encoding="utf-8")
    candidate_path.write_text(json.dumps(_graph(["example.net"], profile="byok")), encoding="utf-8")

    code = main([
        "providers", "compare",
        "--baseline", str(baseline_path),
        "--candidate", str(candidate_path),
        "--case", str(FIXTURES / "cases" / "example.json"),
        "--json",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["new_true_positives"] == ["example.net"]
    assert payload["network_calls_performed"] == 0
    assert payload["eligible_for_planner_learning"] is False
