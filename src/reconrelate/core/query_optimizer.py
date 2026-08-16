"""Deterministic evidence-gap allocation for bounded relationship pivots."""

from __future__ import annotations

from dataclasses import dataclass

from reconrelate.core.types import PivotCandidate

POLICY_VERSION = "pivot-utility-v1"

_SPECIFICITY = {
    "email": 1.00,
    "phone": 0.85,
    "tracker": 2.50,
    "ns": 0.25,
    "org": 0.90,
    "name": 0.35,
}


@dataclass(frozen=True, slots=True)
class PivotPlanDecision:
    pivot: PivotCandidate
    evidence_gap: str
    utility: float
    estimated_logical_calls: int
    policy_version: str = POLICY_VERSION


def score_pivot(
    pivot: PivotCandidate, *, tracker_verification_candidates: int = 3
) -> PivotPlanDecision:
    """Score a pivot without pretending that unmeasured provider recall is known."""
    gap = "corporate_control" if pivot.id_type in {"org", "name"} else "asset_discovery"
    calls = 1 + max(0, tracker_verification_candidates) if pivot.id_type == "tracker" else 1
    specificity = _SPECIFICITY.get(pivot.id_type, 0.20)
    utility = max(0.0, min(1.0, float(pivot.score))) * specificity / calls
    return PivotPlanDecision(
        pivot=pivot,
        evidence_gap=gap,
        utility=round(utility, 6),
        estimated_logical_calls=calls,
    )


def allocate_pivots(
    pivots: list[PivotCandidate],
    top_k: int,
    *,
    tracker_verification_candidates: int = 3,
) -> list[PivotPlanDecision]:
    """Allocate one slot per evidence gap first, then fill remaining slots by utility."""
    if top_k <= 0:
        return []
    decisions = [
        score_pivot(pivot, tracker_verification_candidates=tracker_verification_candidates)
        for pivot in pivots
    ]
    decisions.sort(
        key=lambda item: (
            -item.utility,
            -float(item.pivot.score),
            item.pivot.id_type,
            item.pivot.value,
        )
    )
    chosen: list[PivotPlanDecision] = []
    chosen_keys: set[tuple[str, str]] = set()
    for gap in ("asset_discovery", "corporate_control"):
        candidate = next((item for item in decisions if item.evidence_gap == gap), None)
        if candidate is not None and len(chosen) < top_k:
            chosen.append(candidate)
            chosen_keys.add((candidate.pivot.id_type, candidate.pivot.value))
    for decision in decisions:
        key = (decision.pivot.id_type, decision.pivot.value)
        if len(chosen) >= top_k:
            break
        if key not in chosen_keys:
            chosen.append(decision)
            chosen_keys.add(key)
    return chosen
