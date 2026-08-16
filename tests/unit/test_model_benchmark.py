import asyncio
import json
from datetime import datetime, timezone

import pytest

from reconrelate.cli.app import main
from reconrelate.core.types import PivotCandidate
from reconrelate.llm_orchestration.model_telemetry import ModelCallTelemetry
from reconrelate.quality.model_benchmark import (
    ModelBenchmarkManifest,
    run_model_benchmark,
)


def _write_case(tmp_path, *, corpus_class="synthetic", abstain=False):
    case = {
        "schema_version": 1,
        "case_id": "case-1",
        "domain": "example.com",
        "corpus_class": corpus_class,
        "evidence": {
            "domain": "example.com",
            "whois": {},
            "basic_intel": {"description": "Operated by Example Holdings"},
            "subdomains": [],
        },
        "expected_abstain": abstain,
        "expected_pivots": [] if abstain else [{
            "id_type": "org", "value": "Example Holdings", "source_refs": ["case://source-1"]
        }],
    }
    (tmp_path / "case.json").write_text(json.dumps(case), encoding="utf-8")
    manifest = {"schema_version": 1, "benchmark_id": "bench-1", "cases": ["case.json"]}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


class FakeClient:
    model = "ollama/fake"

    def __init__(self, telemetry, *, disposition="accepted"):
        self.telemetry = telemetry
        self.disposition = disposition

    async def call_unified(self, domain, evidence):  # noqa: ANN001
        now = datetime.now(timezone.utc).isoformat()
        self.telemetry.append(ModelCallTelemetry(
            run_id=None, domain=domain, model=self.model, task="relationship_pivot",
            policy_version="relationship-pivot-v2", cloud=False, status="success",
            reserved_input_tokens=100, reserved_output_tokens=512, reserved_cloud_tokens=0,
            actual_input_tokens=20, actual_output_tokens=10, actual_total_tokens=30,
            provider_reported_cost_usd=None, latency_ms=5, error_class=None, error_message=None,
            started_at=now, completed_at=now, output_disposition=self.disposition,
        ))
        if self.disposition == "abstained":
            return []
        return [PivotCandidate("org", "Example Holdings", 0.9, "model")]


def test_matched_benchmark_measures_assisted_recall_lift_but_synthetic_is_ineligible(tmp_path) -> None:
    manifest = ModelBenchmarkManifest.from_path(_write_case(tmp_path))
    telemetry = []
    result = asyncio.run(run_model_benchmark(manifest, FakeClient(telemetry), telemetry))
    assert result.baseline.recall == 0.0
    assert result.assisted.recall == 1.0
    assert result.recall_lift == 1.0
    assert result.recommendation_eligible is False
    assert "corpus contains synthetic cases" in result.ineligibility_reasons


def test_abstention_is_scored_separately(tmp_path) -> None:
    manifest = ModelBenchmarkManifest.from_path(_write_case(tmp_path, abstain=True))
    telemetry = []
    result = asyncio.run(run_model_benchmark(
        manifest, FakeClient(telemetry, disposition="abstained"), telemetry
    ))
    assert result.abstention_accuracy == 1.0
    assert result.invalid_or_error_count == 0


def test_case_requires_provenance_for_every_expected_pivot(tmp_path) -> None:
    path = _write_case(tmp_path)
    case_path = tmp_path / "case.json"
    payload = json.loads(case_path.read_text(encoding="utf-8"))
    payload["expected_pivots"][0]["source_refs"] = []
    case_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="source_refs"):
        ModelBenchmarkManifest.from_path(path)


def test_case_rejects_unknown_evidence_and_accepts_labeled_aliases(tmp_path) -> None:
    path = _write_case(tmp_path)
    case_path = tmp_path / "case.json"
    payload = json.loads(case_path.read_text(encoding="utf-8"))
    payload["expected_pivots"][0]["acceptable_values"] = ["Example Holdings Inc."]
    case_path.write_text(json.dumps(payload), encoding="utf-8")
    case = ModelBenchmarkManifest.from_path(path).cases[0]
    assert len(case.expected_pivots[0].keys) == 2

    payload["evidence"]["raw"] = "unbounded"
    case_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown fields"):
        ModelBenchmarkManifest.from_path(path)


def test_cloud_benchmark_requires_explicit_approval_before_sdk(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("RECONRELATE_CONFIG_PATH", str(tmp_path / "absent-config.json"))
    monkeypatch.setenv("RECONRELATE_LLM_ALLOW_CLOUD", "true")
    manifest = _write_case(tmp_path)
    assert main([
        "models", "benchmark", "--manifest", str(manifest), "--model", "gpt-5-mini"
    ]) == 2
    assert "requires --approve-cloud" in capsys.readouterr().err
