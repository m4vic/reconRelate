"""Deterministic policy for projecting evidence into explainable relationship claims."""

from __future__ import annotations

from dataclasses import dataclass

from reconrelate.core.evidence import Claim, ConfidenceClass


POLICY_VERSION = "relationship-v1"


@dataclass(frozen=True, slots=True)
class ProjectedClaim:
    claim: Claim
    evidence_weight: float
    evidence_reason: str


def project_relationship(
    *,
    relation_type: str,
    subject_type: str,
    subject_value: str,
    object_type: str,
    object_value: str,
    score: float,
    source: str,
) -> ProjectedClaim:
    bounded = max(0.0, min(float(score), 1.0))
    if relation_type in {
        "domain_has_subdomain", "domain_has_ip", "domain_has_mx", "domain_has_ns",
        "domain_has_cname",
    }:
        confidence_class: ConfidenceClass = "verified"
        status = "observed"
    elif relation_type.startswith("acquisition_"):
        confidence_class = "probable" if bounded >= 0.75 else "candidate"
        status = relation_type.removeprefix("acquisition_")
    else:
        confidence_class = "probable" if bounded >= 0.8 else "candidate"
        status = "candidate"
    claim = Claim.build(
        claim_type=relation_type,
        subject_type=subject_type,
        subject_value_norm=subject_value,
        object_type=object_type,
        object_value_norm=object_value,
        status=status,
        confidence_class=confidence_class,
        score=bounded,
        policy_version=POLICY_VERSION,
    )
    return ProjectedClaim(
        claim=claim,
        evidence_weight=bounded,
        evidence_reason=f"{source} observation projected by {POLICY_VERSION}",
    )


def project_domain_relationship(
    *, relation_type: str, subject_domain: str, object_domain: str, score: float, source: str
) -> ProjectedClaim:
    return project_relationship(
        relation_type=relation_type,
        subject_type="domain",
        subject_value=subject_domain,
        object_type="domain",
        object_value=object_domain,
        score=score,
        source=source,
    )
