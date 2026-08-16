"""Rebuild a deterministic relationship graph solely from persisted claims and evidence."""

from __future__ import annotations

from typing import Any


def project_claim_graph(claims: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    node_keys: set[tuple[str, str]] = set()
    edges: list[dict[str, Any]] = []
    for claim in claims:
        if claim.get("confidence_class") == "rejected":
            continue
        subject = (str(claim["subject_type"]), str(claim["subject_value_norm"]))
        object_ = (str(claim["object_type"]), str(claim["object_value_norm"]))
        node_keys.update((subject, object_))
        evidence = claim.get("evidence") or []
        sources = sorted({str(item.get("source", "unknown")) for item in evidence})
        polarities = sorted({str(item.get("polarity", "supports")) for item in evidence})
        edges.append({
            "from": {"type": subject[0], "value": subject[1]},
            "to": {"type": object_[0], "value": object_[1]},
            "relation_type": str(claim["claim_type"]),
            "status": str(claim["status"]),
            "confidence_class": str(claim["confidence_class"]),
            "score": float(claim["score"]),
            "policy_version": str(claim["policy_version"]),
            "evidence_sources": sources,
            "evidence_polarities": polarities,
        })
    nodes = [{"type": node_type, "value": value} for node_type, value in sorted(node_keys)]
    edges.sort(key=lambda edge: (
        edge["from"]["type"], edge["from"]["value"], edge["relation_type"],
        edge["to"]["type"], edge["to"]["value"],
    ))
    return {"nodes": nodes, "edges": edges}
