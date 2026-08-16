"""Offline aggregation of matched provider comparisons across a labeled corpus."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from reconrelate.quality.comparison import (
    MATCHED_POLICY_FIELDS,
    MIN_LABELED_DOMAINS,
    MIN_POSITIVE_DOMAINS,
    MatchedComparison,
    compare_graphs,
)
from reconrelate.quality.evaluation import EvaluationCase


def _load_object(path: Path, label: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object: {path}")
    return value


def _metric(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    schema_version: int
    benchmark_id: str
    case_count: int
    labeled_domain_count: int
    positive_domain_count: int
    negative_domain_count: int
    baseline_true_positives: int
    candidate_true_positives: int
    baseline_known_false_positives: int
    candidate_known_false_positives: int
    baseline_micro_precision: float | None
    candidate_micro_precision: float | None
    baseline_micro_recall: float | None
    candidate_micro_recall: float | None
    baseline_micro_f1: float | None
    candidate_micro_f1: float | None
    new_true_positives: tuple[str, ...]
    lost_true_positives: tuple[str, ...]
    new_known_false_positives: tuple[str, ...]
    resolved_known_false_positives: tuple[str, ...]
    candidate_added_unlabeled: tuple[str, ...]
    usage_delta: dict[str, float | int]
    incremental_calls_per_net_new_true_positive: float | None
    incremental_billable_units_per_net_new_true_positive: float | None
    verdict: str
    eligible_for_planner_learning: bool
    learning_gate_reasons: tuple[str, ...]
    cases: tuple[MatchedComparison, ...]
    offline: bool = True
    network_calls_performed: int = 0
    model_calls_performed: int = 0
    billable_calls_performed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_benchmark(manifest_path: str | Path) -> BenchmarkResult:
    path = Path(manifest_path)
    manifest = _load_object(path, "benchmark manifest")
    if manifest.get("schema_version") != 1:
        raise ValueError("benchmark manifest schema_version must be 1")
    benchmark_id = str(manifest.get("benchmark_id") or "").strip()
    if not benchmark_id:
        raise ValueError("benchmark manifest requires benchmark_id")
    entries = manifest.get("comparisons")
    if not isinstance(entries, list) or not entries:
        raise ValueError("benchmark manifest requires a non-empty comparisons array")

    comparisons: list[MatchedComparison] = []
    case_ids: set[str] = set()
    roots: set[str] = set()
    policy_values: dict[str, set[str]] = {field: set() for field in MATCHED_POLICY_FIELDS}
    base = path.resolve().parent
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"comparisons[{index}] must be an object")
        required = {name: str(entry.get(name) or "").strip() for name in ("case", "baseline", "candidate")}
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"comparisons[{index}] missing: {', '.join(missing)}")
        case = EvaluationCase.from_path(base / required["case"])
        if case.case_id in case_ids:
            raise ValueError(f"duplicate benchmark case_id: {case.case_id}")
        if case.root_domain in roots:
            raise ValueError(f"duplicate benchmark root_domain: {case.root_domain}")
        case_ids.add(case.case_id)
        roots.add(case.root_domain)
        baseline = _load_object(base / required["baseline"], "baseline graph")
        candidate = _load_object(base / required["candidate"], "candidate graph")
        comparison = compare_graphs(baseline, candidate, case)
        comparisons.append(comparison)
        for field in comparison.matched_policy_fields:
            policy_values[field].add(repr(baseline.get("run", {}).get(field)))

    inconsistent = [field for field, values in policy_values.items() if len(values) > 1]
    if inconsistent:
        raise ValueError(
            "benchmark cases use inconsistent matched policies: " + ", ".join(inconsistent)
        )

    positive_count = sum(item.candidate.labeled_positive_count for item in comparisons)
    negative_count = sum(item.candidate.labeled_negative_count for item in comparisons)
    baseline_tp = sum(len(item.baseline.true_positives) for item in comparisons)
    candidate_tp = sum(len(item.candidate.true_positives) for item in comparisons)
    baseline_fp = sum(len(item.baseline.known_false_positives) for item in comparisons)
    candidate_fp = sum(len(item.candidate.known_false_positives) for item in comparisons)
    baseline_precision = _metric(baseline_tp, baseline_tp + baseline_fp)
    candidate_precision = _metric(candidate_tp, candidate_tp + candidate_fp)
    baseline_recall = _metric(baseline_tp, positive_count)
    candidate_recall = _metric(candidate_tp, positive_count)

    def f1(precision: float | None, recall: float | None) -> float | None:
        if precision is None or recall is None or precision + recall == 0:
            return None
        return 2 * precision * recall / (precision + recall)

    def scoped(attribute: str) -> tuple[str, ...]:
        return tuple(sorted(
            f"{item.root_domain}/{domain}"
            for item in comparisons for domain in getattr(item, attribute)
        ))

    new_tp = scoped("new_true_positives")
    lost_tp = scoped("lost_true_positives")
    new_fp = scoped("new_known_false_positives")
    resolved_fp = scoped("resolved_known_false_positives")
    unlabeled = scoped("candidate_added_unlabeled")
    usage_delta = {
        key: sum(item.usage_delta[key] for item in comparisons)
        for key in ("calls", "upstream_requests", "latency_ms", "billable_units")
    }
    net_new_tp = len(new_tp) - len(lost_tp)
    calls_per_tp = float(usage_delta["calls"]) / net_new_tp if net_new_tp > 0 else None
    units_per_tp = float(usage_delta["billable_units"]) / net_new_tp if net_new_tp > 0 else None

    degraded_cases = [item.case_id for item in comparisons if item.verdict == "degraded"]
    reasons: list[str] = []
    label_count = positive_count + negative_count
    if label_count < MIN_LABELED_DOMAINS:
        reasons.append(f"requires at least {MIN_LABELED_DOMAINS} labeled domains; corpus has {label_count}")
    if positive_count < MIN_POSITIVE_DOMAINS:
        reasons.append(f"requires at least {MIN_POSITIVE_DOMAINS} positive domains; corpus has {positive_count}")
    if unlabeled:
        reasons.append("candidate-only predictions remain unlabeled")
    missing_policy = sorted({field for item in comparisons for field in item.unverified_policy_fields})
    if missing_policy:
        reasons.append("matched policy fields missing: " + ", ".join(missing_policy))
    if degraded_cases:
        reasons.append("case-level degradation: " + ", ".join(degraded_cases))

    if degraded_cases or new_fp or lost_tp:
        verdict = "degraded"
    elif new_tp or resolved_fp:
        verdict = "improved_on_labeled_corpus"
    elif unlabeled:
        verdict = "inconclusive_unlabeled_change"
    else:
        verdict = "no_labeled_change"
    return BenchmarkResult(
        schema_version=1,
        benchmark_id=benchmark_id,
        case_count=len(comparisons),
        labeled_domain_count=label_count,
        positive_domain_count=positive_count,
        negative_domain_count=negative_count,
        baseline_true_positives=baseline_tp,
        candidate_true_positives=candidate_tp,
        baseline_known_false_positives=baseline_fp,
        candidate_known_false_positives=candidate_fp,
        baseline_micro_precision=baseline_precision,
        candidate_micro_precision=candidate_precision,
        baseline_micro_recall=baseline_recall,
        candidate_micro_recall=candidate_recall,
        baseline_micro_f1=f1(baseline_precision, baseline_recall),
        candidate_micro_f1=f1(candidate_precision, candidate_recall),
        new_true_positives=new_tp,
        lost_true_positives=lost_tp,
        new_known_false_positives=new_fp,
        resolved_known_false_positives=resolved_fp,
        candidate_added_unlabeled=unlabeled,
        usage_delta=usage_delta,
        incremental_calls_per_net_new_true_positive=calls_per_tp,
        incremental_billable_units_per_net_new_true_positive=units_per_tp,
        verdict=verdict,
        eligible_for_planner_learning=not reasons,
        learning_gate_reasons=tuple(reasons),
        cases=tuple(comparisons),
    )


def render_benchmark(result: BenchmarkResult) -> str:
    def metric(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.3f}"

    lines = [
        f"Provider benchmark: {result.benchmark_id}",
        "Offline analysis: 0 database, network, model, or billable calls",
        f"Cases: {result.case_count}; labels: {result.labeled_domain_count} "
        f"({result.positive_domain_count} positive, {result.negative_domain_count} negative)",
        f"Verdict: {result.verdict}",
        f"Micro precision: {metric(result.baseline_micro_precision)} -> "
        f"{metric(result.candidate_micro_precision)}",
        f"Micro recall: {metric(result.baseline_micro_recall)} -> "
        f"{metric(result.candidate_micro_recall)}",
        f"Micro F1: {metric(result.baseline_micro_f1)} -> {metric(result.candidate_micro_f1)}",
        f"New/lost true positives: {len(result.new_true_positives)}/{len(result.lost_true_positives)}",
        f"New/resolved known false positives: "
        f"{len(result.new_known_false_positives)}/{len(result.resolved_known_false_positives)}",
        f"Candidate-only unlabeled: {len(result.candidate_added_unlabeled)}",
        "Usage delta: " + ", ".join(
            f"{key}={value:+g}" for key, value in result.usage_delta.items()
        ),
        f"Eligible for planner learning: {'yes' if result.eligible_for_planner_learning else 'no'}",
    ]
    lines.extend(f"  - {reason}" for reason in result.learning_gate_reasons)
    return "\n".join(lines)
