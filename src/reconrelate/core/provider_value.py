"""Offline, non-causal provider contribution accounting for one run."""

from __future__ import annotations

from collections import defaultdict

from reconrelate.core.source_independence import CATALOG_VERSION, UNCLASSIFIED, source_family


def build_provider_value_report(graph: dict) -> dict:
    contributions: dict[str, dict[str, object]] = defaultdict(lambda: {
        "supporting_claims": 0,
        "verified_claims_supported": 0,
        "sole_family_claims": 0,
        "sole_family_verified_claims": 0,
        "corroborated_claims": 0,
        "supported_domains": set(),
        "sole_family_supported_domains": set(),
        "sources": set(),
    })
    for claim in graph.get("claims", []):
        supporting = [item for item in claim.get("evidence", []) if item.get("polarity") == "supports"]
        families: dict[str, set[str]] = defaultdict(set)
        for evidence in supporting:
            source = str(evidence.get("source", ""))
            family = str(evidence.get("source_family") or source_family(source))
            families[family].add(source)
        classified = {family for family in families if family != UNCLASSIFIED}
        sole = len(classified) == 1 and UNCLASSIFIED not in families
        corroborated = len(classified) >= 2 and UNCLASSIFIED not in families
        is_verified = claim.get("confidence_class") == "verified"
        domain_object = (
            str(claim.get("object_value_norm", ""))
            if claim.get("object_type") == "domain" else ""
        )
        for family, sources in families.items():
            row = contributions[family]
            row["supporting_claims"] += 1
            row["sources"].update(sources)
            if is_verified:
                row["verified_claims_supported"] += 1
            if sole and family in classified:
                row["sole_family_claims"] += 1
                if is_verified:
                    row["sole_family_verified_claims"] += 1
                if domain_object:
                    row["sole_family_supported_domains"].add(domain_object)
            if corroborated:
                row["corroborated_claims"] += 1
            if domain_object:
                row["supported_domains"].add(domain_object)

    family_rows: list[dict] = []
    for family, raw in sorted(contributions.items()):
        family_rows.append({
            "source_family": family,
            "sources": sorted(raw["sources"]),
            "supporting_claims": raw["supporting_claims"],
            "verified_claims_supported": raw["verified_claims_supported"],
            "sole_family_claims": raw["sole_family_claims"],
            "sole_family_verified_claims": raw["sole_family_verified_claims"],
            "supported_domains": sorted(raw["supported_domains"]),
            "sole_family_supported_domains": sorted(raw["sole_family_supported_domains"]),
            "corroborated_claims": raw["corroborated_claims"],
        })

    usage_rows = []
    for usage in graph.get("provider_usage", []):
        usage_rows.append({
            "provider": str(usage.get("provider", "")),
            "source_family": source_family(str(usage.get("provider", ""))),
            "capability": str(usage.get("capability", "")),
            "status": str(usage.get("status", "")),
            "calls": int(usage.get("calls", 0)),
            "attempts": int(usage.get("attempts", 0)),
            "upstream_requests": int(usage.get("upstream_requests", 0)),
            "pages": int(usage.get("pages", 0)),
            "latency_ms": int(usage.get("latency_ms", 0)),
            "billable_units": float(usage.get("units", 0.0)),
        })
    return {
        "run_id": str(graph.get("run", {}).get("id", "")),
        "root_domain": str(graph.get("run", {}).get("root_domain", "")),
        "offline": True,
        "network_calls_performed": 0,
        "model_calls_performed": 0,
        "billable_calls_performed": 0,
        "source_family_catalog": CATALOG_VERSION,
        "family_contributions": family_rows,
        "provider_usage": usage_rows,
        "interpretation": {
            "sole_family_support_is_causal_lift": False,
            "unclassified_sources_receive_sole_family_credit": False,
            "paid_vs_free_lift_requires_matched_benchmark_runs": True,
        },
    }


def render_provider_value_report(report: dict) -> str:
    lines = [
        f"Provider value report: {report['run_id']} ({report['root_domain']})",
        "Offline analysis: 0 network calls, 0 model calls, 0 billable calls",
        "Sole-family support is attribution, not causal lift.",
        "",
        "Evidence-family contribution:",
    ]
    rows = report["family_contributions"]
    if not rows:
        lines.append("  No evidence-backed claim contributions recorded.")
    for row in rows:
        lines.append(
            f"  {row['source_family']}: claims={row['supporting_claims']}, "
            f"verified={row['verified_claims_supported']}, sole={row['sole_family_claims']}, "
            f"sole_verified={row['sole_family_verified_claims']}, "
            f"domains={len(row['supported_domains'])}, sources={','.join(row['sources'])}"
        )
    lines.extend(["", "Recorded provider usage:"])
    usage = report["provider_usage"]
    if not usage:
        lines.append("  No provider telemetry recorded.")
    for row in usage:
        lines.append(
            f"  {row['provider']}/{row['capability']} [{row['source_family']}]: "
            f"{row['status']}, calls={row['calls']}, requests={row['upstream_requests']}, "
            f"units={row['billable_units']:g}, latency={row['latency_ms']}ms"
        )
    return "\n".join(lines)
