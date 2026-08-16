"""Offline provider-manifest preflight planning. No provider instances or network calls."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from reconrelate.config.settings import Settings
from reconrelate.data_gathering.registry import PAID, ProviderInfo, ProviderRegistry


@dataclass(frozen=True, slots=True)
class PlanStep:
    capability: str
    providers: tuple[str, ...]
    condition: str
    logical_calls_per_domain: int
    upstream_requests_per_call: int
    billable: bool
    source_families: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class QueryPlan:
    profile: str
    paid_approved: bool
    max_domains: int
    max_provider_calls: int
    max_billable_units: float
    selected_providers: tuple[str, ...]
    unavailable_providers: tuple[str, ...]
    policy_excluded_providers: tuple[str, ...]
    approval_gated_providers: tuple[str, ...]
    steps: tuple[PlanStep, ...]
    worst_case_logical_calls: int
    worst_case_upstream_requests: int
    worst_case_billable_units: float
    ceiling_warnings: tuple[str, ...]

    def to_dict(self) -> dict:
        value = asdict(self)
        value["steps"] = [step.to_dict() for step in self.steps]
        value["network_calls_performed"] = 0
        value["billable_calls_performed"] = 0
        return value


def _available(infos: list[ProviderInfo]) -> list[ProviderInfo]:
    return [info for info in infos if info.available()]


def build_query_plan(settings: Settings, registry: ProviderRegistry, *, paid_approved: bool = False) -> QueryPlan:
    profile = settings.provider_tier if settings.provider_tier in {"free", "byok"} else "free"
    infos = registry.infos()
    available = _available(infos)
    unavailable = [info for info in infos if not info.available()]
    policy_excluded = [info for info in available if profile == "free" and (info.billable or info.tier == PAID)]
    approval_gated = [
        info for info in available
        if profile == "byok" and (info.billable or info.tier == PAID) and not paid_approved
    ]
    usable = [
        info for info in available
        if info not in policy_excluded and info not in approval_gated
    ]

    by_cap: dict[str, list[ProviderInfo]] = {}
    for info in usable:
        by_cap.setdefault(info.capability, []).append(info)
    for values in by_cap.values():
        values.sort(key=lambda info: 0 if info.tier == PAID else 1)

    steps: list[PlanStep] = []

    def add(capability: str, providers: list[ProviderInfo], condition: str, calls: int) -> None:
        if not providers or calls <= 0:
            return
        max_requests = max(info.effective_request_limit() for info in providers)
        steps.append(PlanStep(
            capability=capability,
            providers=tuple(info.name for info in providers),
            condition=condition,
            logical_calls_per_domain=calls,
            upstream_requests_per_call=max_requests,
            billable=any(info.billable for info in providers),
            source_families=tuple(sorted({info.source_family for info in providers})),
        ))

    add("whois", by_cap.get("whois", []), "always; ordered registration cascade", len(by_cap.get("whois", [])))
    for capability in ("basic_info", "dns"):
        selected = by_cap.get(capability, [])[:1]
        add(capability, selected, "always", 1)
    if settings.map_subdomains:
        add("subdomains", by_cap.get("subdomains", []), "apex domains within enumeration depth; waterfall", len(by_cap.get("subdomains", [])))
    if settings.historical_web:
        add("historical_web", by_cap.get("historical_web", [])[:1], "explicit --history opt-in", 1)
    reverse = by_cap.get("reverse_whois", [])[:1]
    add("reverse_whois", reverse, "conditional on selected non-organization pivots", settings.pivot_top_k)
    if reverse and by_cap.get("basic_info"):
        add(
            "tracker_verification", by_cap["basic_info"][:1],
            "conditional worst case: every pivot is a tracker and every search result needs verification",
            settings.pivot_top_k * settings.max_domains_per_identifier,
        )
    if settings.expand_acquisitions:
        add(
            "acquisitions", by_cap.get("acquisitions", []),
            "conditional on selected organization pivots",
            len(by_cap.get("acquisitions", [])) * settings.pivot_top_k,
        )

    per_domain_calls = sum(step.logical_calls_per_domain for step in steps)
    per_domain_requests = sum(
        step.logical_calls_per_domain * step.upstream_requests_per_call for step in steps
    )
    billable_per_domain = sum(
        step.logical_calls_per_domain * (settings.retry_count + 1)
        for step in steps if step.billable
    )
    max_domains = settings.global_max_nodes
    worst_calls = per_domain_calls * max_domains
    worst_requests = per_domain_requests * max_domains
    worst_billable = float(billable_per_domain * max_domains)
    warnings: list[str] = []
    if worst_calls > settings.max_provider_calls:
        warnings.append(
            f"logical-call worst case {worst_calls} exceeds hard ceiling {settings.max_provider_calls}; "
            "the executor will stop additional calls"
        )
    if worst_billable > settings.max_billable_units:
        warnings.append(
            f"billable-unit worst case {worst_billable:g} exceeds hard ceiling "
            f"{settings.max_billable_units:g}; the executor will stop paid calls"
        )
    return QueryPlan(
        profile=profile,
        paid_approved=paid_approved,
        max_domains=max_domains,
        max_provider_calls=settings.max_provider_calls,
        max_billable_units=settings.max_billable_units,
        selected_providers=tuple(f"{info.capability}:{info.name}" for info in usable),
        unavailable_providers=tuple(f"{info.capability}:{info.name}" for info in unavailable),
        policy_excluded_providers=tuple(f"{info.capability}:{info.name}" for info in policy_excluded),
        approval_gated_providers=tuple(f"{info.capability}:{info.name}" for info in approval_gated),
        steps=tuple(steps),
        worst_case_logical_calls=worst_calls,
        worst_case_upstream_requests=worst_requests,
        worst_case_billable_units=worst_billable,
        ceiling_warnings=tuple(warnings),
    )


def render_query_plan(plan: QueryPlan) -> str:
    lines = [
        f"Provider preflight plan ({plan.profile})",
        "Network calls performed: 0; billable calls performed: 0",
        f"Hard ceilings: {plan.max_provider_calls} provider calls, {plan.max_billable_units:g} billable units",
        f"Graph domain ceiling: {plan.max_domains}",
        "",
        "Planned steps (upper bounds per processed domain):",
    ]
    for step in plan.steps:
        lines.append(
            f"  {step.capability:<22} calls={step.logical_calls_per_domain:<4} "
            f"upstream/call<={step.upstream_requests_per_call:<3} "
            f"providers={','.join(step.providers)}; families={','.join(step.source_families)}; "
            f"{step.condition}"
        )
    lines.extend([
        "",
        f"Whole-run worst case: {plan.worst_case_logical_calls} logical calls, "
        f"{plan.worst_case_upstream_requests} upstream requests, "
        f"{plan.worst_case_billable_units:g} billable units",
    ])
    if plan.policy_excluded_providers:
        lines.append("Policy excluded: " + ", ".join(plan.policy_excluded_providers))
    if plan.approval_gated_providers:
        lines.append("Approval gated: " + ", ".join(plan.approval_gated_providers))
    if plan.unavailable_providers:
        lines.append("Unavailable: " + ", ".join(plan.unavailable_providers))
    for warning in plan.ceiling_warnings:
        lines.append("WARNING: " + warning)
    return "\n".join(lines)
