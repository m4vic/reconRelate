import json

import pytest

from reconrelate.cli.app import main
from reconrelate.quality.benchmark import run_benchmark


def _write_json(path, value) -> None:  # noqa: ANN001
    path.write_text(json.dumps(value), encoding="utf-8")


def _graph(root: str, domains: list[str], profile: str, calls: int) -> dict:
    return {
        "run": {
            "id": f"{root}-{profile}", "root_domain": root, "provider_profile": profile,
            "max_depth": 1, "pivot_top_k": 3, "run_mode": "quick",
            "llm_model": "ollama/test", "llm_policy_version": "deterministic-escalation-v1",
            "cache_mode": "refresh",
            "cloud_approved": 0, "max_model_calls": 50,
            "max_model_input_tokens": 200000, "max_model_output_tokens": 25600,
            "max_cloud_tokens": 0,
            "fast_model": "", "model_routing_policy": "single-model-v1",
            "max_cloud_cost_microusd": 0,
            "model_price_catalog_version": "openai-2026.08.14-v1",
        },
        "nodes": [
            {"id": str(index), "node_type": "domain", "value_norm": domain}
            for index, domain in enumerate([root, *domains])
        ],
        "provider_usage": [{
            "provider": profile, "capability": "test", "status": "success", "calls": calls,
            "attempts": calls, "upstream_requests": calls, "pages": calls,
            "latency_ms": calls * 10, "units": calls if profile == "byok" else 0,
        }],
    }


def _entry(tmp_path, name: str) -> dict:  # noqa: ANN001
    root = f"{name}.com"
    positives = [f"{name}-owned-{index}.net" for index in range(5)]
    negatives = [f"{name}-noise-{index}.org" for index in range(5)]
    case = {
        "schema_version": 1, "case_id": f"{name}-case", "root_domain": root,
        "labels": [
            {"domain": domain, "classification": "positive", "relationship": "owned",
             "source_refs": [f"fixture://{name}/{domain}"]}
            for domain in positives
        ] + [
            {"domain": domain, "classification": "negative", "relationship": "unrelated",
             "source_refs": [f"fixture://{name}/{domain}"]}
            for domain in negatives
        ],
    }
    paths = {
        "case": tmp_path / f"{name}.case.json",
        "baseline": tmp_path / f"{name}.free.json",
        "candidate": tmp_path / f"{name}.byok.json",
    }
    _write_json(paths["case"], case)
    _write_json(paths["baseline"], _graph(root, [], "free", 4))
    _write_json(paths["candidate"], _graph(root, positives, "byok", 5))
    return {key: path.name for key, path in paths.items()}


def test_benchmark_pools_labeled_outcomes_and_passes_corpus_gate(tmp_path) -> None:
    manifest = tmp_path / "benchmark.json"
    _write_json(manifest, {
        "schema_version": 1,
        "benchmark_id": "two-org-v1",
        "comparisons": [_entry(tmp_path, "alpha"), _entry(tmp_path, "beta")],
    })

    result = run_benchmark(manifest)

    assert result.case_count == 2
    assert result.labeled_domain_count == 20
    assert result.positive_domain_count == 10
    assert result.candidate_true_positives == 10
    assert result.candidate_micro_precision == 1.0
    assert result.candidate_micro_recall == 1.0
    assert result.usage_delta["calls"] == 2
    assert result.incremental_calls_per_net_new_true_positive == 0.2
    assert result.eligible_for_planner_learning is True
    assert result.verdict == "improved_on_labeled_corpus"


def test_benchmark_rejects_duplicate_roots(tmp_path) -> None:
    entry = _entry(tmp_path, "alpha")
    duplicate_case = tmp_path / "duplicate.case.json"
    value = json.loads((tmp_path / entry["case"]).read_text(encoding="utf-8"))
    value["case_id"] = "different-id"
    _write_json(duplicate_case, value)
    duplicate = {**entry, "case": duplicate_case.name}
    manifest = tmp_path / "benchmark.json"
    _write_json(manifest, {
        "schema_version": 1, "benchmark_id": "bad", "comparisons": [entry, duplicate],
    })

    with pytest.raises(ValueError, match="duplicate benchmark root_domain"):
        run_benchmark(manifest)


def test_cli_benchmark_is_offline_json(tmp_path, capsys) -> None:
    manifest = tmp_path / "benchmark.json"
    _write_json(manifest, {
        "schema_version": 1, "benchmark_id": "one-org-v1",
        "comparisons": [_entry(tmp_path, "alpha")],
    })

    assert main(["providers", "benchmark", "--manifest", str(manifest), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["benchmark_id"] == "one-org-v1"
    assert payload["network_calls_performed"] == 0
    assert payload["eligible_for_planner_learning"] is False
