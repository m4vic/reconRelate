from reconrelate.core.claim_projection import POLICY_VERSION, project_domain_relationship, project_relationship


def test_subdomain_observation_projects_verified_claim() -> None:
    result = project_domain_relationship(
        relation_type="domain_has_subdomain",
        subject_domain="example.com",
        object_domain="api.example.com",
        score=0.9,
        source="crtsh",
    )
    assert result.claim.confidence_class == "verified"
    assert result.claim.status == "observed"
    assert result.claim.policy_version == POLICY_VERSION


def test_relationship_score_is_bounded_and_classified() -> None:
    result = project_domain_relationship(
        relation_type="related_domain_via_identifier",
        subject_domain="example.com",
        object_domain="example.org",
        score=7.0,
        source="whoxy",
    )
    assert result.claim.score == 1.0
    assert result.claim.confidence_class == "probable"
    assert "whoxy" in result.evidence_reason


def test_infrastructure_relationship_preserves_object_type() -> None:
    result = project_relationship(
        relation_type="domain_has_ip", subject_type="domain", subject_value="example.com",
        object_type="ip", object_value="93.184.216.34", score=0.8, source="system-dns",
    )
    assert result.claim.object_type == "ip"
    assert result.claim.object_value_norm == "93.184.216.34"
    assert result.claim.confidence_class == "verified"
