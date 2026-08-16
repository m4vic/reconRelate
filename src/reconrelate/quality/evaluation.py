"""Offline, provenance-aware evaluation of a saved ReconRelate graph."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from reconrelate.core.normalize import normalize_domain, registrable_domain


LabelClass = Literal["positive", "negative"]


@dataclass(frozen=True, slots=True)
class DomainLabel:
    domain: str
    classification: LabelClass
    relationship: str
    source_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    schema_version: int
    case_id: str
    root_domain: str
    labels: tuple[DomainLabel, ...]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvaluationCase":
        if payload.get("schema_version") != 1:
            raise ValueError("evaluation case schema_version must be 1")
        case_id = str(payload.get("case_id") or "").strip()
        root_domain = _root_domain(payload.get("root_domain"))
        if not case_id:
            raise ValueError("evaluation case requires a non-empty case_id")

        raw_labels = payload.get("labels")
        if not isinstance(raw_labels, list) or not raw_labels:
            raise ValueError("evaluation case requires a non-empty labels array")

        labels: list[DomainLabel] = []
        seen: dict[str, str] = {}
        for index, raw in enumerate(raw_labels):
            if not isinstance(raw, dict):
                raise ValueError(f"labels[{index}] must be an object")
            domain = _root_domain(raw.get("domain"))
            classification = str(raw.get("classification") or "").strip().lower()
            if classification not in {"positive", "negative"}:
                raise ValueError(f"labels[{index}].classification must be positive or negative")
            relationship = str(raw.get("relationship") or "").strip()
            if not relationship:
                raise ValueError(f"labels[{index}] requires a relationship")
            refs = raw.get("source_refs")
            if not isinstance(refs, list) or not refs or not all(str(ref).strip() for ref in refs):
                raise ValueError(f"labels[{index}] requires non-empty source_refs")
            if domain == root_domain:
                raise ValueError("root_domain must not also appear in labels")
            previous = seen.get(domain)
            if previous and previous != classification:
                raise ValueError(f"domain {domain!r} has conflicting labels")
            if previous:
                raise ValueError(f"domain {domain!r} is labeled more than once")
            seen[domain] = classification
            labels.append(DomainLabel(domain, classification, relationship, tuple(map(str, refs))))
        return cls(1, case_id, root_domain, tuple(labels))

    @classmethod
    def from_path(cls, path: str | Path) -> "EvaluationCase":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("evaluation case must be a JSON object")
        return cls.from_dict(payload)


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    schema_version: int
    case_id: str
    root_domain: str
    predicted_count: int
    labeled_positive_count: int
    labeled_negative_count: int
    true_positives: tuple[str, ...]
    false_negatives: tuple[str, ...]
    known_false_positives: tuple[str, ...]
    unlabeled_predictions: tuple[str, ...]
    labeled_precision: float | None
    recall: float | None
    f1: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _root_domain(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("domain must be non-empty")
    return registrable_domain(normalize_domain(raw))


def predicted_root_domains(graph: dict[str, Any]) -> set[str]:
    """Return normalized non-root registrable domains from a graph export."""
    run = graph.get("run")
    nodes = graph.get("nodes")
    if not isinstance(run, dict) or not isinstance(nodes, list):
        raise ValueError("graph must contain run object and nodes array")
    root = _root_domain(run.get("root_domain"))
    predicted: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict) or node.get("node_type") != "domain":
            continue
        try:
            domain = _root_domain(node.get("value_norm"))
        except (TypeError, ValueError):
            continue
        if domain != root:
            predicted.add(domain)
    return predicted


def evaluate_graph(graph: dict[str, Any], case: EvaluationCase) -> EvaluationResult:
    graph_root = _root_domain(graph.get("run", {}).get("root_domain"))
    if graph_root != case.root_domain:
        raise ValueError(
            f"graph root {graph_root!r} does not match evaluation case root {case.root_domain!r}"
        )

    predicted = predicted_root_domains(graph)
    positives = {label.domain for label in case.labels if label.classification == "positive"}
    negatives = {label.domain for label in case.labels if label.classification == "negative"}
    true_positives = predicted & positives
    false_negatives = positives - predicted
    known_false_positives = predicted & negatives
    unlabeled = predicted - positives - negatives

    labeled_prediction_count = len(true_positives) + len(known_false_positives)
    precision = (
        len(true_positives) / labeled_prediction_count if labeled_prediction_count else None
    )
    recall = len(true_positives) / len(positives) if positives else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall > 0
        else None
    )
    return EvaluationResult(
        schema_version=1,
        case_id=case.case_id,
        root_domain=case.root_domain,
        predicted_count=len(predicted),
        labeled_positive_count=len(positives),
        labeled_negative_count=len(negatives),
        true_positives=tuple(sorted(true_positives)),
        false_negatives=tuple(sorted(false_negatives)),
        known_false_positives=tuple(sorted(known_false_positives)),
        unlabeled_predictions=tuple(sorted(unlabeled)),
        labeled_precision=precision,
        recall=recall,
        f1=f1,
    )


def render_evaluation(result: EvaluationResult) -> str:
    def metric(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.3f}"

    lines = [
        f"Evaluation: {result.case_id}",
        f"Root: {result.root_domain}",
        f"Predicted domains: {result.predicted_count}",
        f"Labeled precision: {metric(result.labeled_precision)}",
        f"Recall: {metric(result.recall)}",
        f"F1: {metric(result.f1)}",
        f"True positives ({len(result.true_positives)}): {', '.join(result.true_positives) or '-'}",
        f"Known false positives ({len(result.known_false_positives)}): "
        f"{', '.join(result.known_false_positives) or '-'}",
        f"False negatives ({len(result.false_negatives)}): {', '.join(result.false_negatives) or '-'}",
        f"Unlabeled predictions ({len(result.unlabeled_predictions)}): "
        f"{', '.join(result.unlabeled_predictions) or '-'}",
    ]
    return "\n".join(lines)
