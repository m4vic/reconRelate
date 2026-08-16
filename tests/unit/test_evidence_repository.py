import pytest

from reconrelate.core.evidence import Claim, Observation
from reconrelate.db.db import get_connection, init_db
from reconrelate.db.repositories import GraphRepository


def _repo_and_run() -> tuple[GraphRepository, str]:
    conn = get_connection(":memory:")
    init_db(conn)
    repo = GraphRepository(conn)
    return repo, repo.create_run("example.com", 1, 3)


def test_observation_replay_is_idempotent_but_new_snapshot_is_preserved() -> None:
    repo, run_id = _repo_and_run()
    first = Observation.build(
        subject_type="domain", subject_value_norm="example.com", predicate="registered_by",
        object_type="organization", object_value_norm="Example Inc", source="rdap",
        observed_at="2026-01-01T00:00:00+00:00", confidence=0.8,
        normalized={"registrar": "Example Registrar"}, idempotency_key="rdap-record-1",
    )
    assert repo.add_observation(run_id, first) == repo.add_observation(run_id, first)
    second = Observation.build(
        subject_type="domain", subject_value_norm="example.com", predicate="registered_by",
        object_type="organization", object_value_norm="New Example Inc", source="rdap",
        observed_at="2026-02-01T00:00:00+00:00", confidence=0.8,
        idempotency_key="rdap-record-2",
    )
    repo.add_observation(run_id, second)
    rows = repo.get_observations(run_id, subject_type="domain", subject_value_norm="example.com")
    assert len(rows) == 2
    assert rows[0]["normalized"] == {"registrar": "Example Registrar"}


def test_claim_provenance_includes_supporting_and_contradicting_evidence() -> None:
    repo, run_id = _repo_and_run()
    support = Observation.build(
        subject_type="company", subject_value_norm="buyer", predicate="acquired",
        object_type="company", object_value_norm="target", source="sec_filing",
        observed_at="2026-01-01T00:00:00+00:00", idempotency_key="filing-1",
    )
    contradict = Observation.build(
        subject_type="company", subject_value_norm="buyer", predicate="acquired",
        object_type="company", object_value_norm="target", source="news",
        observed_at="2026-01-02T00:00:00+00:00", idempotency_key="news-1",
    )
    support_id = repo.add_observation(run_id, support)
    contradict_id = repo.add_observation(run_id, contradict)
    claim = Claim.build(
        claim_type="acquisition", subject_type="company", subject_value_norm="buyer",
        object_type="company", object_value_norm="target", status="completed",
        confidence_class="probable", score=0.85, policy_version="acquisition-v1",
    )
    claim_id = repo.add_claim(run_id, claim)
    assert claim_id == repo.add_claim(run_id, claim)
    repo.link_claim_evidence(claim_id, support_id, "supports", 1.0, "Regulatory filing")
    repo.link_claim_evidence(claim_id, contradict_id, "contradicts", 0.4, "Later correction")
    repo.link_claim_evidence(claim_id, support_id, "supports", 1.0, "Regulatory filing")
    result = repo.get_claims_with_evidence(run_id)
    assert len(result) == 1
    assert {item["polarity"] for item in result[0]["evidence"]} == {"supports", "contradicts"}
    assert {item["source"] for item in result[0]["evidence"]} == {"sec_filing", "news"}
    assert result[0]["evidence_independence"]["independence_status"] == "unclassified"
    assert {item["source_family"] for item in result[0]["evidence"]} == {"unclassified"}


def test_evidence_models_and_links_validate_scores() -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        Observation.build(
            subject_type="domain", subject_value_norm="example.com", predicate="seen",
            source="test", confidence=1.1,
        )
    with pytest.raises(ValueError, match="invalid claim confidence"):
        Claim.build(
            claim_type="ownership", subject_type="domain", subject_value_norm="example.com",
            object_type="company", object_value_norm="Example", status="active",
            confidence_class="certain", score=1.0, policy_version="v1",  # type: ignore[arg-type]
        )
    repo, _ = _repo_and_run()
    with pytest.raises(ValueError, match="between 0 and 1"):
        repo.link_claim_evidence("claim", "observation", "supports", -0.1, "bad")
