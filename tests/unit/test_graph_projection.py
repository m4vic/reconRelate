from reconrelate.core.graph_projection import project_claim_graph


def test_claim_graph_is_deterministic_and_excludes_rejected_claims() -> None:
    base = {
        "status": "observed", "confidence_class": "verified", "score": 0.8,
        "policy_version": "relationship-v1",
    }
    claims = [
        {
            **base, "claim_type": "domain_has_ip", "subject_type": "domain",
            "subject_value_norm": "example.com", "object_type": "ip",
            "object_value_norm": "93.184.216.34",
            "evidence": [{"source": "system-dns", "polarity": "supports"}],
        },
        {
            **base, "claim_type": "related_domain", "subject_type": "domain",
            "subject_value_norm": "example.com", "object_type": "domain",
            "object_value_norm": "noise.example", "confidence_class": "rejected",
            "evidence": [{"source": "review", "polarity": "contradicts"}],
        },
    ]
    projected = project_claim_graph(list(reversed(claims)))
    assert projected["nodes"] == [
        {"type": "domain", "value": "example.com"},
        {"type": "ip", "value": "93.184.216.34"},
    ]
    assert len(projected["edges"]) == 1
    assert projected["edges"][0]["evidence_sources"] == ["system-dns"]
