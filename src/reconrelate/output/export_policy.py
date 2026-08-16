"""Fail-closed provider-policy filtering for portable graph artifacts."""

from __future__ import annotations

import copy
from typing import Any


def _restricted_evidence_reference(item: dict[str, Any]) -> dict[str, Any]:
    """Keep attribution/scoring without redistributing provider-returned field values."""
    allowed = {
        "observation_id", "polarity", "weight", "reason", "created_at", "source",
        "predicate", "observed_at", "source_family", "data_policy_version",
        "export_scope", "raw_retention",
    }
    return {key: value for key, value in item.items() if key in allowed}


def prepare_graph_export(graph: dict[str, Any]) -> dict[str, Any]:
    """Return a portable graph with every observation's export policy enforced."""
    exported = copy.deepcopy(graph)
    observations = exported.get("observations", [])
    normalized: list[dict[str, Any]] = []
    restricted_count = 0
    denied_count = 0
    for item in observations if isinstance(observations, list) else []:
        if not isinstance(item, dict):
            continue
        scope = str(item.get("export_scope") or "normalized")
        if scope == "normalized":
            normalized.append(item)
        elif scope == "derived_only":
            restricted_count += 1
        else:
            denied_count += 1
    exported["observations"] = normalized

    for claim in exported.get("claims", []):
        if not isinstance(claim, dict):
            continue
        evidence_out: list[dict[str, Any]] = []
        for evidence in claim.get("evidence", []):
            if not isinstance(evidence, dict):
                continue
            scope = str(evidence.get("export_scope") or "normalized")
            if scope == "normalized":
                evidence_out.append(evidence)
            elif scope == "derived_only":
                evidence_out.append(_restricted_evidence_reference(evidence))
        claim["evidence"] = evidence_out

    exported["provider_data_export"] = {
        "policy_enforced": True,
        "restricted_observations_omitted": restricted_count,
        "non_exportable_observations_omitted": denied_count,
    }
    return exported
