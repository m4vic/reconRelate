from __future__ import annotations

import json

from reconrelate.output.export_policy import prepare_graph_export


def render_graph_json(graph: dict) -> str:
    return json.dumps(prepare_graph_export(graph), indent=2, sort_keys=True)


def _node_label(node: dict) -> str:
    if node["node_type"] == "domain":
        return str(node["value_norm"])
    metadata = json.loads(node.get("metadata_json", "{}") or "{}")
    return f"[{metadata.get('identifier_type', 'identifier')}] {node['value_norm']}"


def render_ascii_tree(graph: dict) -> str:
    """Human-friendly tree for quick terminal review."""
    run = graph["run"]
    nodes = graph["nodes"]
    edges = graph["edges"]
    node_by_id = {node["id"]: node for node in nodes}

    root_node_id = None
    for node in nodes:
        if node["node_type"] == "domain" and node["value_norm"] == run["root_domain"]:
            root_node_id = node["id"]
            break
    if root_node_id is None:
        return "(no root node found)"

    domain_to_identifiers: dict[str, list[str]] = {}
    identifier_to_domains: dict[str, list[str]] = {}
    domain_to_domains: dict[str, list[tuple[str, str]]] = {}  # direct domain→domain (acquisitions)
    for edge in edges:
        rt = edge["relation_type"]
        if rt in {"domain_has_identifier", "llm_selected_pivot"}:
            domain_to_identifiers.setdefault(edge["from_node_id"], []).append(edge["to_node_id"])
        elif rt == "identifier_links_domain":
            identifier_to_domains.setdefault(edge["from_node_id"], []).append(edge["to_node_id"])
        elif rt.startswith("acquisition_") or rt == "related_domain":
            domain_to_domains.setdefault(edge["from_node_id"], []).append((edge["to_node_id"], rt))

    lines: list[str] = []
    visited_domains: set[str] = set()

    def walk_domain(domain_node_id: str, prefix: str, note: str = "") -> None:
        node = node_by_id.get(domain_node_id)
        if not node:
            return
        suffix = f"  ({note})" if note else ""
        lines.append(f"{prefix}{_node_label(node)}{suffix}")
        if domain_node_id in visited_domains:
            lines.append(f"{prefix}  (already visited)")
            return
        visited_domains.add(domain_node_id)

        # Direct related/acquired domains (Wikidata P856) — the reliable, high-signal links.
        for child_domain_id, rel in sorted(set(domain_to_domains.get(domain_node_id, []))):
            if child_domain_id == domain_node_id:
                continue
            walk_domain(child_domain_id, prefix + "  ", note=rel.replace("acquisition_", "").replace("_", " "))

        for identifier_id in sorted(set(domain_to_identifiers.get(domain_node_id, []))):
            identifier_node = node_by_id.get(identifier_id)
            if not identifier_node:
                continue
            lines.append(f"{prefix}  {_node_label(identifier_node)}")
            for child_domain_id in sorted(set(identifier_to_domains.get(identifier_id, []))):
                if child_domain_id == domain_node_id:
                    continue
                walk_domain(child_domain_id, prefix + "    ")

    walk_domain(root_node_id, "")
    return "\n".join(lines)


def render_markdown_report(graph: dict) -> str:
    run = graph["run"]
    nodes = graph["nodes"]
    edges = graph["edges"]
    pivots = graph.get("pivot_decisions", [])
    observations = graph.get("observations", [])
    claims = graph.get("claims", [])
    provider_usage = graph.get("provider_usage", [])
    model_usage = graph.get("model_usage", [])
    model_budget_usage = graph.get("model_budget_usage", {})
    task_summary = graph.get("task_summary", {})

    domains_count = len([node for node in nodes if node["node_type"] == "domain"])
    identifiers_count = len([node for node in nodes if node["node_type"] == "identifier"])

    lines: list[str] = []
    lines.append(f"# ReconRelate Report: {run['id']}")
    lines.append("")
    lines.append("## Run")
    lines.append(f"- Root domain: `{run['root_domain']}`")
    lines.append(f"- Status: `{run['status']}`")
    md = run["max_depth"]
    try:
        md_int = int(md) if md is not None else -1
    except (TypeError, ValueError):
        md_int = 0
    depth_display = "unlimited" if md_int < 0 else f"`{md_int}`"
    lines.append(f"- Max depth: {depth_display}")
    if "provider_profile" in run:
        actual_calls = sum(int(item.get("calls", 0)) for item in provider_usage)
        actual_billable_units = sum(float(item.get("units", 0.0)) for item in provider_usage)
        lines.append(f"- Provider profile: `{run['provider_profile']}`")
        lines.append(
            f"- Provider calls: `{actual_calls}` / `{int(run['max_provider_calls'])}` hard ceiling"
        )
        lines.append(
            f"- Billable units: `{actual_billable_units:.2f}` / "
            f"`{float(run['max_billable_units']):.2f}` hard ceiling"
        )
    if "max_model_calls" in run:
        lines.append(f"- Model: `{run['llm_model']}` (policy `{run['llm_policy_version']}`)")
        if run.get("fast_model"):
            lines.append(
                f"- Fast model: `{run['fast_model']}` (routing `{run['model_routing_policy']}`)"
            )
        lines.append(f"- Cloud approved: `{bool(run['cloud_approved'])}`")
        lines.append(
            f"- Model ceilings: calls `{int(run['max_model_calls'])}`, "
            f"input tokens `{int(run['max_model_input_tokens'])}`, "
            f"output tokens `{int(run['max_model_output_tokens'])}`, "
            f"cloud tokens `{int(run['max_cloud_tokens'])}`, cloud cost "
            f"`${int(run.get('max_cloud_cost_microusd', 0)) / 1_000_000:.6f}` "
            f"(catalog `{run.get('model_price_catalog_version', 'legacy')}`)"
        )
        if model_budget_usage:
            lines.append(
                "- Durable model reservations: "
                f"calls `{int(model_budget_usage.get('calls', 0))}` / `{int(run['max_model_calls'])}`, "
                f"input `{int(model_budget_usage.get('input_tokens', 0))}` / "
                f"`{int(run['max_model_input_tokens'])}`, output "
                f"`{int(model_budget_usage.get('output_tokens', 0))}` / "
                f"`{int(run['max_model_output_tokens'])}`, cloud "
                f"`{int(model_budget_usage.get('cloud_tokens', 0))}` / "
                f"`{int(run['max_cloud_tokens'])}`, cost "
                f"`${int(model_budget_usage.get('cloud_cost_microusd', 0)) / 1_000_000:.6f}` / "
                f"`${int(run.get('max_cloud_cost_microusd', 0)) / 1_000_000:.6f}`"
            )
    lines.append("")
    lines.append("## Graph Summary")
    lines.append(f"- Domains: `{domains_count}`")
    lines.append(f"- Identifiers: `{identifiers_count}`")
    lines.append(f"- Edges: `{len(edges)}`")
    lines.append(f"- Source observations: `{len(observations)}`")
    lines.append(f"- Evidence-backed claims: `{len(claims)}`")
    if task_summary:
        lines.append(
            "- Durable tasks: "
            + ", ".join(
                f"{status} `{int(task_summary.get(status, 0))}`"
                for status in ("pending", "in_progress", "succeeded", "failed")
            )
        )
    lines.append("")
    lines.append("## Provider Usage")
    if not provider_usage:
        lines.append("- No provider calls recorded (the run may predate usage accounting or be cache-only).")
    else:
        for usage in provider_usage:
            billing = f", units `{float(usage['units']):.2f}`" if usage.get("billable") else ""
            lines.append(
                "- "
                + f"`{usage['provider']}` / `{usage['capability']}`: `{usage['status']}`, "
                + f"calls `{usage['calls']}`, attempts `{usage['attempts']}`, "
                + f"requests `{usage.get('upstream_requests', 0)}`, pages `{usage.get('pages', 0)}`, "
                + f"latency `{usage['latency_ms']}ms`{billing}"
            )
    lines.append("")
    lines.append("## Model Usage")
    if not model_usage:
        lines.append("- No model calls recorded (deterministic path, legacy run, or no escalation).")
    else:
        for usage in model_usage:
            actual = usage.get("actual_total_tokens")
            actual_display = "unknown" if actual is None else str(int(actual))
            cost = usage.get("provider_reported_cost_usd")
            cost_display = "unknown" if cost is None else f"${float(cost):.6f}"
            disposition = usage.get("output_disposition") or "none"
            egress = usage.get("egress_policy_version") or "legacy"
            reserved_cost = int(usage.get("reserved_cloud_cost_microusd") or 0) / 1_000_000
            lines.append(
                f"- `{usage['model']}` / `{usage['task']}`: `{usage['status']}`, "
                f"output `{disposition}`, egress `{egress}`, "
                f"calls `{int(usage['calls'])}`, reserved input/output/cloud "
                f"`{int(usage['reserved_input_tokens'])}`/"
                f"`{int(usage['reserved_output_tokens'])}`/"
                f"`{int(usage['reserved_cloud_tokens'])}`, actual total `{actual_display}`, "
                f"reserved cloud cost `${reserved_cost:.6f}`, "
                f"provider-reported cost `{cost_display}`, latency `{int(usage['latency_ms'])}ms`"
            )
    lines.append("")
    lines.append("## Evidence Sources")
    source_counts: dict[str, int] = {}
    for observation in observations:
        source = str(observation.get("source", "unknown"))
        source_counts[source] = source_counts.get(source, 0) + 1
    if not source_counts:
        lines.append("- No normalized source observations recorded.")
    else:
        for source, count in sorted(source_counts.items()):
            lines.append(f"- `{source}`: `{count}` observations")
    lines.append("")
    lines.append("## Relationship Claims")
    if not claims:
        lines.append("- No evidence-backed relationship claims recorded.")
    else:
        for claim in claims[:20]:
            lines.append(
                "- "
                + f"`{claim['subject_value_norm']}` -> `{claim['object_value_norm']}`: "
                + f"`{claim['claim_type']}` / `{claim['confidence_class']}` "
                + f"(score `{float(claim['score']):.2f}`, policy `{claim['policy_version']}`)"
            )
            independence = claim.get("evidence_independence", {})
            if independence:
                lines.append(
                    "  - Evidence independence: "
                    + f"`{independence['independence_status']}`; families "
                    + f"`{', '.join(independence['families']) or 'none'}` "
                    + f"(catalog `{independence['catalog_version']}`)"
                )
            evidence = claim.get("evidence", [])
            if not evidence:
                lines.append("  - Evidence: none linked.")
            for item in evidence:
                lines.append(
                    "  - "
                    + f"{item['polarity']} via `{item['source']}` "
                    + f"(family `{item.get('source_family', 'unclassified')}`) "
                    + f"(`{item['predicate']}`, weight `{float(item['weight']):.2f}`): "
                    + str(item["reason"])
                )
    lines.append("")
    lines.append("## Top Pivot Decisions")
    if not pivots:
        lines.append("- No pivot decisions recorded.")
    else:
        for pivot in pivots[:10]:
            planning = ""
            if "utility" in pivot:
                planning = (
                    f", utility `{float(pivot['utility']):.3f}`, gap `{pivot['evidence_gap']}`, "
                    f"estimated calls `{int(pivot['estimated_logical_calls'])}`, "
                    f"policy `{pivot['policy_version']}`"
                )
            lines.append(
                "- "
                + f"`{pivot['identifier_type']}` `{pivot['identifier_value_norm']}` "
                + f"(score `{pivot['score']:.2f}`{planning}): {pivot['reason_short']}"
            )
    return "\n".join(lines)
