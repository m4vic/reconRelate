"""Matched deterministic-versus-model pivot benchmark over saved evidence."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from reconrelate.core.normalize import normalize_domain, normalize_identifier
from reconrelate.core.types import BasicIntelRecord, PivotCandidate, WhoisRecord
from reconrelate.llm_orchestration.deterministic_scorer import extract_deterministic_pivots
from reconrelate.llm_orchestration.model_telemetry import ModelCallTelemetry
from reconrelate.llm_orchestration.relationship_engine import LLMClient, is_cloud_model
from reconrelate.llm_orchestration.response_parser import validate_pivot

MIN_ELIGIBLE_CASES = 20
MIN_ELIGIBLE_POSITIVE_CASES = 10
MIN_ELIGIBLE_ABSTENTION_CASES = 5
MIN_RECALL_LIFT = 0.05


@dataclass(frozen=True, slots=True)
class ExpectedPivot:
    id_type: str
    value: str
    acceptable_values: tuple[str, ...]
    source_refs: tuple[str, ...]

    @property
    def keys(self) -> frozenset[tuple[str, str]]:
        return frozenset(
            (self.id_type, normalize_identifier(self.id_type, value))
            for value in (self.value, *self.acceptable_values)
        )


def _validate_evidence(evidence: dict[str, Any]) -> None:
    allowed_top = {"domain", "whois", "basic_intel", "subdomains", "subdomains_truncated"}
    allowed_whois = {
        "registrant_name", "registrant_org", "registrant_email", "registrant_phone",
        "nameservers", "creation_date", "expiration_date",
    }
    allowed_intel = {
        "title", "description", "aliases", "copyright_org", "tracker_ids",
        "redirect_domain", "legal_entities",
    }
    unknown = set(evidence) - allowed_top
    if unknown:
        raise ValueError(f"evidence contains unknown fields: {sorted(unknown)}")
    for section, allowed, arrays in (
        ("whois", allowed_whois, {"nameservers"}),
        ("basic_intel", allowed_intel, {"aliases", "tracker_ids", "legal_entities"}),
    ):
        value = evidence.get(section, {})
        if not isinstance(value, dict):
            raise ValueError(f"evidence.{section} must be an object")
        extra = set(value) - allowed
        if extra:
            raise ValueError(f"evidence.{section} contains unknown fields: {sorted(extra)}")
        for key, item in value.items():
            if key in arrays:
                if not isinstance(item, list) or not all(isinstance(entry, str) for entry in item):
                    raise ValueError(f"evidence.{section}.{key} must be a string array")
            elif item is not None and not isinstance(item, str):
                raise ValueError(f"evidence.{section}.{key} must be a string or null")
    subs = evidence.get("subdomains", [])
    if not isinstance(subs, list) or not all(isinstance(item, str) for item in subs):
        raise ValueError("evidence.subdomains must be a string array")


@dataclass(frozen=True, slots=True)
class ModelBenchmarkCase:
    case_id: str
    domain: str
    corpus_class: Literal["synthetic", "held_out"]
    evidence: dict[str, Any]
    expected_pivots: tuple[ExpectedPivot, ...]
    expected_abstain: bool

    @classmethod
    def from_path(cls, path: str | Path) -> "ModelBenchmarkCase":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("model benchmark case schema_version must be 1")
        case_id = str(payload.get("case_id") or "").strip()
        if not case_id:
            raise ValueError("model benchmark case requires case_id")
        domain = normalize_domain(str(payload.get("domain") or ""))
        corpus_class = str(payload.get("corpus_class") or "")
        if corpus_class not in {"synthetic", "held_out"}:
            raise ValueError("corpus_class must be synthetic or held_out")
        evidence = payload.get("evidence")
        if not isinstance(evidence, dict):
            raise ValueError("model benchmark case requires an evidence object")
        _validate_evidence(evidence)
        evidence_domain = normalize_domain(str(evidence.get("domain") or ""))
        if evidence_domain != domain:
            raise ValueError("evidence.domain must match domain")
        expected_abstain = payload.get("expected_abstain")
        if not isinstance(expected_abstain, bool):
            raise ValueError("expected_abstain must be boolean")
        raw_pivots = payload.get("expected_pivots")
        if not isinstance(raw_pivots, list):
            raise ValueError("expected_pivots must be an array")
        pivots: list[ExpectedPivot] = []
        seen: set[tuple[str, str]] = set()
        for index, item in enumerate(raw_pivots):
            if not isinstance(item, dict):
                raise ValueError(f"expected_pivots[{index}] must be an object")
            id_type = str(item.get("id_type") or "").strip()
            value = str(item.get("value") or "").strip()
            refs = item.get("source_refs")
            if not isinstance(refs, list) or not refs or not all(str(ref).strip() for ref in refs):
                raise ValueError(f"expected_pivots[{index}] requires source_refs")
            alternatives = item.get("acceptable_values", [])
            if not isinstance(alternatives, list) or not all(
                isinstance(alt, str) and alt.strip() for alt in alternatives
            ):
                raise ValueError(f"expected_pivots[{index}].acceptable_values must be strings")
            pivot = ExpectedPivot(id_type, value, tuple(alternatives), tuple(map(str, refs)))
            try:
                keys = pivot.keys
            except Exception as exc:
                raise ValueError(f"expected_pivots[{index}] is invalid") from exc
            if not keys or seen.intersection(keys):
                raise ValueError("expected pivots must be unique after normalization")
            seen.update(keys)
            pivots.append(pivot)
        if expected_abstain == bool(pivots):
            raise ValueError("abstention cases require no pivots; non-abstentions require pivots")
        return cls(case_id, domain, corpus_class, evidence, tuple(pivots), expected_abstain)


@dataclass(frozen=True, slots=True)
class ModelBenchmarkManifest:
    benchmark_id: str
    cases: tuple[ModelBenchmarkCase, ...]

    @classmethod
    def from_path(cls, path: str | Path) -> "ModelBenchmarkManifest":
        manifest_path = Path(path).resolve()
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("model benchmark manifest schema_version must be 1")
        benchmark_id = str(payload.get("benchmark_id") or "").strip()
        raw_cases = payload.get("cases")
        if not benchmark_id or not isinstance(raw_cases, list) or not raw_cases:
            raise ValueError("model benchmark manifest requires benchmark_id and cases")
        cases: list[ModelBenchmarkCase] = []
        ids: set[str] = set()
        for raw in raw_cases:
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError("manifest cases must be non-empty relative paths")
            case_path = (manifest_path.parent / raw).resolve()
            case = ModelBenchmarkCase.from_path(case_path)
            if case.case_id in ids:
                raise ValueError(f"duplicate model benchmark case_id: {case.case_id}")
            ids.add(case.case_id)
            cases.append(case)
        return cls(benchmark_id, tuple(cases))


@dataclass(frozen=True, slots=True)
class Score:
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float | None
    recall: float | None
    f1: float | None


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    corpus_class: str
    expected_abstain: bool
    output_disposition: str
    baseline: Score
    assisted: Score
    abstention_correct: bool
    latency_ms: int
    actual_total_tokens: int | None
    provider_reported_cost_usd: float | None


@dataclass(frozen=True, slots=True)
class ModelBenchmarkResult:
    schema_version: int
    benchmark_id: str
    model: str
    cases: tuple[CaseResult, ...]
    baseline: Score
    assisted: Score
    recall_lift: float | None
    precision_delta: float | None
    abstention_accuracy: float | None
    invalid_or_error_count: int
    actual_total_tokens: int | None
    provider_reported_cost_usd: float | None
    recommendation_eligible: bool
    ineligibility_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _score(
    predicted: set[tuple[str, ...]], expected: tuple[frozenset[tuple[str, ...]], ...]
) -> Score:
    matched = [group for group in expected if predicted.intersection(group)]
    allowed = set().union(*expected) if expected else set()
    tp = len(matched)
    fp = len(predicted - allowed)
    fn = len(expected) - tp
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall else None
    )
    return Score(tp, fp, fn, precision, recall, f1)


def _keys(candidates: list[PivotCandidate], threshold: float = 0.4) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    for candidate in candidates:
        if candidate.score < threshold or not validate_pivot(candidate):
            continue
        try:
            result.add((candidate.id_type, normalize_identifier(candidate.id_type, candidate.value)))
        except Exception:
            continue
    return result


def _records(case: ModelBenchmarkCase) -> tuple[WhoisRecord, BasicIntelRecord]:
    whois = case.evidence.get("whois") if isinstance(case.evidence.get("whois"), dict) else {}
    intel = case.evidence.get("basic_intel") if isinstance(case.evidence.get("basic_intel"), dict) else {}
    return (
        WhoisRecord(
            domain=case.domain,
            registrant_name=str(whois.get("registrant_name") or ""),
            registrant_org=str(whois.get("registrant_org") or ""),
            registrant_email=str(whois.get("registrant_email") or ""),
            registrant_phone=str(whois.get("registrant_phone") or ""),
            nameservers=list(whois.get("nameservers") or []),
            creation_date=str(whois.get("creation_date") or ""),
            expiration_date=str(whois.get("expiration_date") or ""),
            raw=dict(whois),
        ),
        BasicIntelRecord(
            domain=case.domain,
            title=str(intel.get("title") or ""),
            description=str(intel.get("description") or ""),
            aliases=list(intel.get("aliases") or []),
            tracker_ids=list(intel.get("tracker_ids") or []),
            copyright_org=str(intel.get("copyright_org") or ""),
            redirect_domain=str(intel.get("redirect_domain") or ""),
            legal_entities=list(intel.get("legal_entities") or []),
        ),
    )


async def run_model_benchmark(
    manifest: ModelBenchmarkManifest, client: LLMClient, telemetry: list[ModelCallTelemetry]
) -> ModelBenchmarkResult:
    case_results: list[CaseResult] = []
    all_expected: list[frozenset[tuple[str, str, str]]] = []
    all_baseline: set[tuple[str, str, str]] = set()
    all_assisted: set[tuple[str, str, str]] = set()
    for case in manifest.cases:
        whois, intel = _records(case)
        baseline_keys = _keys(extract_deterministic_pivots(whois, intel, case.domain))
        before = len(telemetry)
        model_candidates = await client.call_unified(case.domain, case.evidence)
        call = telemetry[-1] if len(telemetry) > before else None
        model_keys = _keys(model_candidates)
        assisted_keys = baseline_keys | model_keys
        expected = tuple(pivot.keys for pivot in case.expected_pivots)
        all_expected.extend(
            frozenset((case.case_id, *key) for key in group) for group in expected
        )
        all_baseline |= {(case.case_id, *key) for key in baseline_keys}
        all_assisted |= {(case.case_id, *key) for key in assisted_keys}
        disposition = call.output_disposition if call and call.output_disposition else (
            "error" if call else "missing"
        )
        case_results.append(CaseResult(
            case_id=case.case_id,
            corpus_class=case.corpus_class,
            expected_abstain=case.expected_abstain,
            output_disposition=disposition,
            baseline=_score(baseline_keys, expected),
            assisted=_score(assisted_keys, expected),
            abstention_correct=(disposition == "abstained") if case.expected_abstain else (
                disposition == "accepted"
            ),
            latency_ms=call.latency_ms if call else 0,
            actual_total_tokens=call.actual_total_tokens if call else None,
            provider_reported_cost_usd=call.provider_reported_cost_usd if call else None,
        ))
    baseline = _score(all_baseline, tuple(all_expected))
    assisted = _score(all_assisted, tuple(all_expected))
    recall_lift = (
        assisted.recall - baseline.recall
        if assisted.recall is not None and baseline.recall is not None else None
    )
    precision_delta = (
        assisted.precision - baseline.precision
        if assisted.precision is not None and baseline.precision is not None else None
    )
    abstention_cases = [item for item in case_results if item.expected_abstain]
    abstention_accuracy = (
        sum(item.abstention_correct for item in abstention_cases) / len(abstention_cases)
        if abstention_cases else None
    )
    invalid_count = sum(
        item.output_disposition in {"invalid", "error", "missing"} for item in case_results
    )
    reasons: list[str] = []
    if any(case.corpus_class != "held_out" for case in manifest.cases):
        reasons.append("corpus contains synthetic cases")
    if len(manifest.cases) < MIN_ELIGIBLE_CASES:
        reasons.append(f"requires at least {MIN_ELIGIBLE_CASES} cases")
    positive_cases = sum(not case.expected_abstain for case in manifest.cases)
    if positive_cases < MIN_ELIGIBLE_POSITIVE_CASES:
        reasons.append(f"requires at least {MIN_ELIGIBLE_POSITIVE_CASES} positive cases")
    if len(abstention_cases) < MIN_ELIGIBLE_ABSTENTION_CASES:
        reasons.append(f"requires at least {MIN_ELIGIBLE_ABSTENTION_CASES} abstention cases")
    if invalid_count:
        reasons.append("all model outputs must be valid and completed")
    if assisted.precision is None or baseline.precision is None or assisted.precision < baseline.precision:
        reasons.append("assisted precision must not regress from baseline")
    if recall_lift is None or recall_lift < MIN_RECALL_LIFT:
        reasons.append(f"recall lift must be at least {MIN_RECALL_LIFT:.2f}")
    cloud = is_cloud_model(client.model)
    costs = [item.provider_reported_cost_usd for item in case_results]
    if cloud and any(value is None for value in costs):
        reasons.append("cloud provider-reported cost must be known for every case")
    tokens = [item.actual_total_tokens for item in case_results]
    return ModelBenchmarkResult(
        schema_version=1,
        benchmark_id=manifest.benchmark_id,
        model=client.model,
        cases=tuple(case_results),
        baseline=baseline,
        assisted=assisted,
        recall_lift=recall_lift,
        precision_delta=precision_delta,
        abstention_accuracy=abstention_accuracy,
        invalid_or_error_count=invalid_count,
        actual_total_tokens=sum(tokens) if all(value is not None for value in tokens) else None,
        provider_reported_cost_usd=(
            sum(costs) if all(value is not None for value in costs) else None
        ),
        recommendation_eligible=not reasons,
        ineligibility_reasons=tuple(reasons),
    )


def render_model_benchmark(result: ModelBenchmarkResult) -> str:
    def metric(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.3f}"

    lines = [
        f"Model benchmark: {result.benchmark_id}",
        f"Model: {result.model}",
        f"Cases: {len(result.cases)}",
        f"Baseline precision/recall/F1: {metric(result.baseline.precision)} / "
        f"{metric(result.baseline.recall)} / {metric(result.baseline.f1)}",
        f"Assisted precision/recall/F1: {metric(result.assisted.precision)} / "
        f"{metric(result.assisted.recall)} / {metric(result.assisted.f1)}",
        f"Recall lift: {metric(result.recall_lift)}",
        f"Precision delta: {metric(result.precision_delta)}",
        f"Abstention accuracy: {metric(result.abstention_accuracy)}",
        f"Invalid/error outputs: {result.invalid_or_error_count}",
        f"Actual total tokens: {result.actual_total_tokens if result.actual_total_tokens is not None else 'unknown'}",
        "Provider-reported cost: " + (
            f"${result.provider_reported_cost_usd:.6f}"
            if result.provider_reported_cost_usd is not None else "unknown"
        ),
        f"Recommendation eligible: {'yes' if result.recommendation_eligible else 'no'}",
    ]
    lines.extend(f"  - {reason}" for reason in result.ineligibility_reasons)
    lines.append("Case outcomes:")
    lines.extend(
        f"  - {case.case_id}: {case.output_disposition}, assisted F1={metric(case.assisted.f1)}, "
        f"latency={case.latency_ms}ms"
        for case in result.cases
    )
    return "\n".join(lines)
