from reconrelate.core.query_optimizer import POLICY_VERSION, allocate_pivots, score_pivot
from reconrelate.core.types import PivotCandidate


def _pivot(kind: str, value: str, score: float) -> PivotCandidate:
    return PivotCandidate(kind, value, score, "test evidence")


def test_same_call_budget_covers_both_available_evidence_gaps() -> None:
    candidates = [
        _pivot("email", "first@example.com", 0.95),
        _pivot("email", "second@example.com", 0.90),
        _pivot("org", "example holdings", 0.75),
    ]

    selected = allocate_pivots(candidates, top_k=2)

    assert {item.evidence_gap for item in selected} == {"asset_discovery", "corporate_control"}
    assert [item.pivot.value for item in selected] == ["first@example.com", "example holdings"]


def test_tracker_utility_accounts_for_required_candidate_verification() -> None:
    tracker = score_pivot(
        _pivot("tracker", "G-ABCDEF12", 0.8), tracker_verification_candidates=4
    )
    email = score_pivot(_pivot("email", "owner@example.com", 0.8))

    assert tracker.estimated_logical_calls == 5
    assert tracker.utility < email.utility
    assert tracker.policy_version == POLICY_VERSION


def test_allocation_is_deterministic_under_input_reordering() -> None:
    candidates = [
        _pivot("phone", "+12025550123", 0.8),
        _pivot("email", "owner@example.com", 0.8),
        _pivot("org", "example inc", 0.8),
    ]

    forward = allocate_pivots(candidates, top_k=2)
    reverse = allocate_pivots(list(reversed(candidates)), top_k=2)

    assert [(item.pivot.id_type, item.pivot.value) for item in forward] == [
        (item.pivot.id_type, item.pivot.value) for item in reverse
    ]
