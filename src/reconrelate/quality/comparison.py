"""Matched, offline comparison of baseline and candidate graph exports."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from reconrelate.quality.evaluation import (
    EvaluationCase,
    EvaluationResult,
    evaluate_graph,
    predicted_root_domains,
)

MIN_LABELED_DOMAINS = 20
MIN_POSITIVE_DOMAINS = 10
MATCHED_POLICY_FIELDS = (
    "max_depth", "pivot_top_k", "run_mode", "llm_model", "llm_policy_version", "cache_mode",
    "cloud_approved", "max_model_calls", "max_model_input_tokens", "max_model_output_tokens",
    "max_cloud_tokens", "fast_model", "model_routing_policy",
    "max_cloud_cost_microusd", "model_price_catalog_version",
)


def _delta(candidate: float | None, baseline: float | None) -> float | None:
    if candidate is None or baseline is None:
        return None
    return candidate - baseline


def _usage(graph: dict[str, Any]) -> dict[str, float | int]:
    rows = graph.get("provider_usage", [])
    return {
        "calls": sum(int(row.get("calls", 0)) for row in rows),
        "upstream_requests": sum(int(row.get("upstream_requests", 0)) for row in rows),
        "latency_ms": sum(int(row.get("latency_ms", 0)) for row in rows),
        "billable_units": sum(float(row.get("units", 0.0)) for row in rows),
    }


@dataclass(frozen=True, slots=True)
class MatchedComparison:
    schema_version: int
    case_id: str
    root_domain: str
    baseline: EvaluationResult
    candidate: EvaluationResult
    candidate_added_predictions: tuple[str, ...]
    candidate_removed_predictions: tuple[str, ...]
    new_true_positives: tuple[str, ...]
    lost_true_positives: tuple[str, ...]
    new_known_false_positives: tuple[str, ...]
    resolved_known_false_positives: tuple[str, ...]
    candidate_added_unlabeled: tuple[str, ...]
    precision_delta: float | None
    recall_delta: float | None
    f1_delta: float | None
    baseline_usage: dict[str, float | int]
    candidate_usage: dict[str, float | int]
    usage_delta: dict[str, float | int]
    incremental_calls_per_new_true_positive: float | None
    incremental_billable_units_per_new_true_positive: float | None
    matched_policy_fields: tuple[str, ...]
    unverified_policy_fields: tuple[str, ...]
    verdict: str
    eligible_for_planner_learning: bool
    learning_gate_reasons: tuple[str, ...]
    offline: bool = True
    network_calls_performed: int = 0
    model_calls_performed: int = 0
    billable_calls_performed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compare_graphs(
    baseline_graph: dict[str, Any], candidate_graph: dict[str, Any], case: EvaluationCase
) -> MatchedComparison:
    baseline_run = baseline_graph.get("run", {})
    candidate_run = candidate_graph.get("run", {})
    mismatches = []
    compared_fields: list[str] = []
    unverified_fields: list[str] = []
    for field in MATCHED_POLICY_FIELDS:
        baseline_value = baseline_run.get(field)
        candidate_value = candidate_run.get(field)
        if baseline_value is not None and candidate_value is not None and baseline_value != candidate_value:
            mismatches.append(f"{field}: {baseline_value!r} != {candidate_value!r}")
        elif baseline_value is not None and candidate_value is not None:
            compared_fields.append(field)
        else:
            unverified_fields.append(field)
    if mismatches:
        raise ValueError("graphs are not a matched comparison: " + "; ".join(mismatches))

    baseline = evaluate_graph(baseline_graph, case)
    candidate = evaluate_graph(candidate_graph, case)
    baseline_predictions = predicted_root_domains(baseline_graph)
    candidate_predictions = predicted_root_domains(candidate_graph)
    added = candidate_predictions - baseline_predictions
    removed = baseline_predictions - candidate_predictions
    new_tp = set(candidate.true_positives) - set(baseline.true_positives)
    lost_tp = set(baseline.true_positives) - set(candidate.true_positives)
    new_fp = set(candidate.known_false_positives) - set(baseline.known_false_positives)
    resolved_fp = set(baseline.known_false_positives) - set(candidate.known_false_positives)
    added_unlabeled = added & set(candidate.unlabeled_predictions)

    if lost_tp or new_fp:
        verdict = "degraded"
    elif new_tp or resolved_fp:
        verdict = "improved_on_labeled_case"
    elif added_unlabeled or removed:
        verdict = "inconclusive_unlabeled_change"
    else:
        verdict = "no_labeled_change"

    reasons: list[str] = []
    label_count = candidate.labeled_positive_count + candidate.labeled_negative_count
    if label_count < MIN_LABELED_DOMAINS:
        reasons.append(f"requires at least {MIN_LABELED_DOMAINS} labeled domains; case has {label_count}")
    if candidate.labeled_positive_count < MIN_POSITIVE_DOMAINS:
        reasons.append(
            f"requires at least {MIN_POSITIVE_DOMAINS} positive domains; case has "
            f"{candidate.labeled_positive_count}"
        )
    if added_unlabeled:
        reasons.append("candidate-only predictions remain unlabeled")
    if unverified_fields:
        reasons.append("matched policy fields missing: " + ", ".join(unverified_fields))

    baseline_usage = _usage(baseline_graph)
    candidate_usage = _usage(candidate_graph)
    usage_delta = {
        key: candidate_usage[key] - baseline_usage[key]
        for key in baseline_usage
    }
    new_tp_count = len(new_tp)
    calls_per_tp = (
        float(usage_delta["calls"]) / new_tp_count if new_tp_count and usage_delta["calls"] >= 0 else None
    )
    units_per_tp = (
        float(usage_delta["billable_units"]) / new_tp_count
        if new_tp_count and usage_delta["billable_units"] >= 0 else None
    )
    return MatchedComparison(
        schema_version=1,
        case_id=case.case_id,
        root_domain=case.root_domain,
        baseline=baseline,
        candidate=candidate,
        candidate_added_predictions=tuple(sorted(added)),
        candidate_removed_predictions=tuple(sorted(removed)),
        new_true_positives=tuple(sorted(new_tp)),
        lost_true_positives=tuple(sorted(lost_tp)),
        new_known_false_positives=tuple(sorted(new_fp)),
        resolved_known_false_positives=tuple(sorted(resolved_fp)),
        candidate_added_unlabeled=tuple(sorted(added_unlabeled)),
        precision_delta=_delta(candidate.labeled_precision, baseline.labeled_precision),
        recall_delta=_delta(candidate.recall, baseline.recall),
        f1_delta=_delta(candidate.f1, baseline.f1),
        baseline_usage=baseline_usage,
        candidate_usage=candidate_usage,
        usage_delta=usage_delta,
        incremental_calls_per_new_true_positive=calls_per_tp,
        incremental_billable_units_per_new_true_positive=units_per_tp,
        matched_policy_fields=tuple(compared_fields),
        unverified_policy_fields=tuple(unverified_fields),
        verdict=verdict,
        eligible_for_planner_learning=not reasons,
        learning_gate_reasons=tuple(reasons),
    )


def render_comparison(result: MatchedComparison) -> str:
    def metric(value: float | None) -> str:
        return "n/a" if value is None else f"{value:+.3f}"

    lines = [
        f"Matched provider comparison: {result.case_id}",
        f"Root: {result.root_domain}",
        "Offline analysis: 0 database, network, model, or billable calls",
        f"Verdict: {result.verdict}",
        f"Metric deltas: precision {metric(result.precision_delta)}, "
        f"recall {metric(result.recall_delta)}, F1 {metric(result.f1_delta)}",
        f"New true positives: {', '.join(result.new_true_positives) or '-'}",
        f"Lost true positives: {', '.join(result.lost_true_positives) or '-'}",
        f"New known false positives: {', '.join(result.new_known_false_positives) or '-'}",
        f"Resolved known false positives: {', '.join(result.resolved_known_false_positives) or '-'}",
        f"Candidate-only unlabeled: {', '.join(result.candidate_added_unlabeled) or '-'}",
        "Usage delta: "
        + ", ".join(f"{key}={value:+g}" for key, value in result.usage_delta.items()),
        "Incremental cost per new labeled true positive: "
        + (
            f"{result.incremental_calls_per_new_true_positive:g} calls, "
            f"{result.incremental_billable_units_per_new_true_positive:g} billable units"
            if result.incremental_calls_per_new_true_positive is not None
            and result.incremental_billable_units_per_new_true_positive is not None
            else "n/a"
        ),
        f"Matched policy fields: {', '.join(result.matched_policy_fields) or 'none'}",
        f"Unverified policy fields: {', '.join(result.unverified_policy_fields) or 'none'}",
        f"Eligible for planner learning: {'yes' if result.eligible_for_planner_learning else 'no'}",
    ]
    lines.extend(f"  - {reason}" for reason in result.learning_gate_reasons)
    return "\n".join(lines)
