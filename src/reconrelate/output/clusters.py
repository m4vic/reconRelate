"""Shared-operator clusters — surface the cross-domain links already in the graph.

The orchestrator stores each pivot as a deduped *identifier* node and links every domain
that carries it. So domains sharing an identifier (a tracker id, a corporate email, a
vanity nameserver) are already connected through one node. This module reads that graph
and presents the actionable view: "these domains are the same operator, tied by <id>."

A shared **tracker** (GA/GA4/GTM/AdSense id) is the strongest same-operator tie, so
clusters are ranked with trackers first. Pure functions over the graph dict — no DB, no I/O.
"""

from __future__ import annotations

import json
from typing import Any

# Signal strength: a shared analytics/tag id ⇒ almost certainly one operator; phone is weakest.
_RANK = {"tracker": 0, "email": 1, "org": 2, "ns": 3, "name": 4, "phone": 5}


def _identifier_type(node: dict[str, Any]) -> str:
    meta = node.get("metadata_json")
    if not meta:
        return ""
    try:
        return str(json.loads(meta).get("identifier_type", ""))
    except (json.JSONDecodeError, TypeError):
        return ""


def compute_shared_clusters(graph: dict[str, Any], min_domains: int = 2) -> list[dict[str, Any]]:
    """Identifiers linking >= min_domains domains, strongest-signal first."""
    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    domains_by_identifier: dict[str, set[str]] = {}
    meta_by_identifier: dict[str, tuple[str, str]] = {}

    for edge in graph.get("edges", []):
        a, b = nodes.get(edge.get("from_node_id")), nodes.get(edge.get("to_node_id"))
        if not a or not b:
            continue
        # An identifier<->domain edge, in either direction.
        if a["node_type"] == "identifier" and b["node_type"] == "domain":
            idn, dom = a, b
        elif a["node_type"] == "domain" and b["node_type"] == "identifier":
            idn, dom = b, a
        else:
            continue
        domains_by_identifier.setdefault(idn["id"], set()).add(dom["value_norm"])
        meta_by_identifier.setdefault(idn["id"], (idn["value_norm"], _identifier_type(idn)))

    clusters: list[dict[str, Any]] = []
    for node_id, domains in domains_by_identifier.items():
        if len(domains) < min_domains:
            continue
        value, id_type = meta_by_identifier.get(node_id, ("", ""))
        clusters.append({"identifier": value, "id_type": id_type, "domains": sorted(domains)})

    clusters.sort(key=lambda c: (_RANK.get(c["id_type"], 9), -len(c["domains"]), c["identifier"]))
    return clusters


def render_clusters(clusters: list[dict[str, Any]]) -> str:
    if not clusters:
        return "No shared-operator clusters yet (no identifier links 2+ domains in this run)."
    lines = ["Shared-operator clusters (domains tied by a common identifier):", ""]
    for c in clusters:
        tag = c["id_type"] or "identifier"
        lines.append(f"[{tag}] {c['identifier']}  ({len(c['domains'])} domains)")
        lines.extend(f"    - {d}" for d in c["domains"])
        lines.append("")
    return "\n".join(lines).rstrip()
