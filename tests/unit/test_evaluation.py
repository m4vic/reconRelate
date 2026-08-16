import json
from pathlib import Path

import pytest

from reconrelate.quality.evaluation import EvaluationCase, evaluate_graph, render_evaluation
from reconrelate.cli.app import main


FIXTURES = Path(__file__).parents[1] / "eval"


def _case() -> EvaluationCase:
    return EvaluationCase.from_path(FIXTURES / "cases" / "example.json")


def _graph() -> dict:
    return json.loads((FIXTURES / "graphs" / "example.graph.json").read_text(encoding="utf-8"))


def test_evaluates_only_labeled_predictions_for_precision() -> None:
    result = evaluate_graph(_graph(), _case())

    assert result.true_positives == ("example.net",)
    assert result.known_false_positives == ("example.org",)
    assert result.unlabeled_predictions == ("example.invalid",)
    assert result.false_negatives == ()
    assert result.labeled_precision == 0.5
    assert result.recall == 1.0
    assert result.f1 == pytest.approx(2 / 3)


def test_collapses_subdomains_and_excludes_root() -> None:
    graph = _graph()
    graph["nodes"].append({"id": "6", "node_type": "domain", "value_norm": "www.example.net"})
    graph["nodes"].append({"id": "7", "node_type": "domain", "value_norm": "api.example.com"})

    result = evaluate_graph(graph, _case())

    assert result.predicted_count == 3
    assert result.true_positives == ("example.net",)


def test_rejects_unproven_labels() -> None:
    payload = {
        "schema_version": 1,
        "case_id": "bad",
        "root_domain": "example.com",
        "labels": [{
            "domain": "example.net",
            "classification": "positive",
            "relationship": "owned",
            "source_refs": [],
        }],
    }

    with pytest.raises(ValueError, match="source_refs"):
        EvaluationCase.from_dict(payload)


def test_rejects_graph_for_different_root() -> None:
    graph = _graph()
    graph["run"]["root_domain"] = "example.org"

    with pytest.raises(ValueError, match="does not match"):
        evaluate_graph(graph, _case())


def test_human_report_marks_unlabeled_predictions() -> None:
    report = render_evaluation(evaluate_graph(_graph(), _case()))

    assert "Labeled precision: 0.500" in report
    assert "Unlabeled predictions (1): example.invalid" in report


def test_cli_evaluates_saved_graph_as_json(capsys) -> None:  # noqa: ANN001
    code = main([
        "eval",
        str(FIXTURES / "graphs" / "example.graph.json"),
        "--case",
        str(FIXTURES / "cases" / "example.json"),
        "--json",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["case_id"] == "synthetic-example-v1"
    assert payload["labeled_precision"] == 0.5
