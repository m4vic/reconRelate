import asyncio

from reconrelate.config.settings import Settings
from reconrelate.core.types import BasicIntelRecord, PivotCandidate, TrackerVerification, WhoisRecord
from reconrelate.core.claim_projection import project_domain_relationship
from reconrelate.core.evidence import Observation
from reconrelate.core.provider_data_policy import WHOXY_DATA_POLICY
from reconrelate.data_gathering.dns_provider import DNSResult
from reconrelate.db.db import get_connection, init_db
from reconrelate.db.repositories import GraphRepository
from reconrelate.orchestrator.orchestrator import DomainQueue, DomainWorkItem, RunOrchestrator
from reconrelate.output.renderers import render_markdown_report
from reconrelate.output.renderers import render_graph_json
import json


class _Whois:
    async def lookup(self, domain: str) -> WhoisRecord:
        return WhoisRecord(
            domain=domain,
            registrant_org="Example Inc",
            registrant_email="security@example.com",
            nameservers=["ns1.example.net"],
            raw={"source": "test-whois"},
        )


class _WhoisFails:
    async def lookup(self, domain: str) -> WhoisRecord:
        raise RuntimeError("WHOIS unavailable")


class _Basic:
    async def lookup(self, domain: str) -> BasicIntelRecord:
        return BasicIntelRecord(
            domain=domain,
            title="Example",
            aliases=["Example"],
            tracker_ids=["G-ABCDEF12"],
            copyright_org="Example Inc",
            raw={"source": "test-html"},
        )


class _DNS:
    async def lookup(self, domain: str) -> DNSResult:
        return DNSResult(domain=domain, a_records=["93.184.216.34"], mx_records=["mail.example.net"])


class _NoSearch:
    async def search(self, *args, **kwargs):
        return []


class _NoPivots:
    async def select_pivots(self, **kwargs):
        return []


class _TrackerReverse:
    async def search(self, identifier, max_results):  # noqa: ANN001
        return ["candidate.example"]


class _TrackerVerifier:
    def __init__(self, matched: bool) -> None:
        self.matched = matched

    async def verify_tracker(self, domain: str, tracker_id: str) -> TrackerVerification:
        return TrackerVerification(domain, tracker_id, self.matched, f"https://{domain}/")


class _EmailPivot:
    async def select_pivots(self, **kwargs):
        return [PivotCandidate("email", "security@example.com", 0.9, "WHOIS registrant email")]


def test_free_provider_results_flow_into_exported_provenance() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    repo = GraphRepository(conn)
    settings = Settings.from_env()
    settings.map_subdomains = False
    settings.cache_ttl_hours = 0
    orchestrator = RunOrchestrator(
        repository=repo,
        whois_provider=_Whois(),
        basic_info_provider=_Basic(),
        reverse_whois_provider=_NoSearch(),
        crtsh_provider=_NoSearch(),
        hackertarget_provider=_NoSearch(),
        dns_provider=_DNS(),
        relationship_engine=_NoPivots(),
        settings=settings,
    )

    summary = asyncio.run(orchestrator.run("example.com", max_depth=0))
    graph = repo.get_run_graph(summary.run_id)
    assert graph["run"]["run_mode"] == settings.run_mode
    assert graph["run"]["llm_policy_version"] == "deterministic-escalation-v1"
    assert graph["run"]["cache_mode"] == "reuse"
    assert graph["observations"]
    assert {row["source"] for row in graph["observations"]} == {
        "test-whois", "http-html", "system-dns",
    }
    predicates = {row["predicate"] for row in graph["observations"]}
    assert {"registered_by_org", "uses_tracker", "resolves_to", "has_mx"} <= predicates
    claims = {claim["claim_type"]: claim for claim in graph["claims"]}
    assert {"domain_has_ip", "domain_has_mx"} <= claims.keys()
    assert claims["domain_has_ip"]["object_type"] == "ip"
    assert claims["domain_has_ip"]["evidence"][0]["source"] == "system-dns"
    assert claims["domain_has_mx"]["confidence_class"] == "verified"
    assert len(graph["claim_projection"]["edges"]) == 2
    assert graph["task_summary"]["succeeded"] == 1
    usage = {(row["provider"], row["capability"]): row for row in graph["provider_usage"]}
    assert set(usage) == {
        ("python-whois", "whois"), ("http-html", "basic_info"), ("system-dns", "dns"),
    }
    assert all(row["calls"] == 1 and row["status"] == "success" for row in usage.values())
    report = render_markdown_report(graph)
    assert "Source observations:" in report
    assert "`test-whois`" in report
    assert "`system-dns`" in report
    assert "## Provider Usage" in report
    assert "Durable tasks:" in report


def test_cross_domain_redirect_is_visible_claim_but_not_run_task() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    repo = GraphRepository(conn)
    settings = Settings.from_env()
    orchestrator = RunOrchestrator(
        repository=repo, whois_provider=None, basic_info_provider=None,
        reverse_whois_provider=None, crtsh_provider=None, hackertarget_provider=None,
        dns_provider=None, relationship_engine=None, settings=settings,
    )
    run_id = repo.create_run("oldbrand.com", 1, 1)
    root_id = repo.get_or_create_node(run_id, "domain", "oldbrand.com", {})
    observation = Observation.build(
        subject_type="domain", subject_value_norm="oldbrand.com",
        predicate="redirects_to_domain", object_type="domain",
        object_value_norm="newbrand.com", source="http-html", confidence=0.9,
        normalized={"capability": "basic_info", "value": "newbrand.com"},
    )
    observation_id = repo.add_observation(run_id, observation)
    orchestrator._add_infrastructure_from_observation(
        run_id=run_id, domain_node_id=root_id, depth=0,
        observation_id=observation_id, observation=observation,
    )
    graph = repo.get_run_graph(run_id)
    assert any(edge["relation_type"] == "domain_redirects_to" for edge in graph["edges"])
    claim = next(item for item in graph["claims"] if item["claim_type"] == "domain_redirects_to")
    assert claim["confidence_class"] == "probable"
    assert graph["task_summary"] == {"pending": 0, "in_progress": 0, "succeeded": 0, "failed": 0}


def _tracker_reverse_run(matched: bool, *, restricted: bool = False):
    conn = get_connection(":memory:")
    init_db(conn)
    repo = GraphRepository(conn)
    settings = Settings.from_env()
    settings.retry_count = 0
    settings.max_domains_per_identifier = 5
    reverse = _TrackerReverse()
    verifier = _TrackerVerifier(matched)
    setattr(reverse, "__reconrelate_provider__", "search-candidate")
    if restricted:
        setattr(reverse, "__reconrelate_provider__", "whoxy")
        setattr(reverse, "__reconrelate_data_policy__", WHOXY_DATA_POLICY)
    setattr(verifier, "__reconrelate_provider__", "http-html")
    orchestrator = RunOrchestrator(
        repository=repo, whois_provider=None, basic_info_provider=verifier,
        reverse_whois_provider=reverse, crtsh_provider=None, hackertarget_provider=None,
        dns_provider=None, relationship_engine=None, settings=settings,
    )
    run_id = repo.create_run("origin.example", 1, 1)
    root_id = repo.get_or_create_node(run_id, "domain", "origin.example", {})
    queue = DomainQueue(repository=repo, run_id=run_id)
    collected: list[dict] = []
    asyncio.run(orchestrator._reverse_whois_batch(
        [PivotCandidate("tracker", "G-ABCDEF12", 0.85, "test")], run_id, root_id,
        DomainWorkItem("origin.example", 0, None), queue, set(), 1, collected,
    ))
    return repo.get_run_graph(run_id), queue, collected


def test_restricted_paid_observation_is_not_cached_or_exported() -> None:
    graph, queue, collected = _tracker_reverse_run(True, restricted=True)
    assert len(queue) == 1  # still usable in the originating run
    assert collected == []  # cannot enter the shared cross-run cache
    restricted = next(item for item in graph["observations"] if item["source"] == "whoxy")
    assert restricted["cache_allowed"] == 0
    assert restricted["export_scope"] == "derived_only"

    exported = json.loads(render_graph_json(graph))
    assert not any(item["source"] == "whoxy" for item in exported["observations"])
    claim = next(item for item in exported["claims"]
                 if item["claim_type"] == "related_domain_via_identifier")
    whxy_reference = next(item for item in claim["evidence"] if item["source"] == "whoxy")
    assert "subject_value_norm" not in whxy_reference
    assert "object_value_norm" not in whxy_reference
    assert whxy_reference["export_scope"] == "derived_only"
    assert exported["provider_data_export"]["restricted_observations_omitted"] == 1


def test_restricted_provider_disables_cross_run_cache_for_the_run() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    repo = GraphRepository(conn)
    settings = Settings.from_env()
    reverse = _TrackerReverse()
    setattr(reverse, "__reconrelate_data_policy__", WHOXY_DATA_POLICY)
    orchestrator = RunOrchestrator(
        repository=repo, whois_provider=None, basic_info_provider=None,
        reverse_whois_provider=reverse, crtsh_provider=None, hackertarget_provider=None,
        dns_provider=None, relationship_engine=_NoPivots(), settings=settings,
    )
    assert orchestrator.cross_run_cache_allowed is False
    summary = asyncio.run(orchestrator.run("origin.example", max_depth=0))
    assert summary.status == "completed"
    assert repo.get_domain_cache("origin.example") is None


def test_tracker_reverse_candidate_requires_and_retains_exact_page_verification() -> None:
    graph, queue, collected = _tracker_reverse_run(True)
    claim = next(item for item in graph["claims"] if item["claim_type"] == "related_domain_via_identifier")
    assert len(claim["evidence"]) == 2
    assert {item["source"] for item in claim["evidence"]} == {"search-candidate", "http-html"}
    assert any(item["predicate"] == "uses_tracker" for item in graph["observations"])
    assert len(collected[0]["supporting_observations"]) == 1
    assert len(queue) == 1


def test_tracker_reverse_candidate_mismatch_is_not_mapped_or_enqueued() -> None:
    graph, queue, collected = _tracker_reverse_run(False)
    assert not any(item["claim_type"] == "related_domain_via_identifier" for item in graph["claims"])
    assert not any(edge["relation_type"] == "identifier_links_domain" for edge in graph["edges"])
    assert len(queue) == 0
    assert collected == []


def test_cached_tracker_relationship_replays_verification_evidence() -> None:
    _, _, collected = _tracker_reverse_run(True)
    conn = get_connection(":memory:")
    init_db(conn)
    repo = GraphRepository(conn)
    settings = Settings.from_env()
    orchestrator = RunOrchestrator(
        repository=repo, whois_provider=None, basic_info_provider=None,
        reverse_whois_provider=None, crtsh_provider=None, hackertarget_provider=None,
        dns_provider=None, relationship_engine=None, settings=settings,
    )
    run_id = repo.create_run("origin.example", 1, 1)
    root_id = repo.get_or_create_node(run_id, "domain", "origin.example", {})
    orchestrator._replay_cached(
        run_id, root_id, DomainWorkItem("origin.example", 0, None), collected,
        DomainQueue(repository=repo, run_id=run_id), set(), 1,
    )
    graph = repo.get_run_graph(run_id)
    claim = next(item for item in graph["claims"] if item["claim_type"] == "related_domain_via_identifier")
    assert len(claim["evidence"]) == 2
    assert any(item["source"] == "http-html" for item in claim["evidence"])


def test_repository_batch_rolls_back_on_failure() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    repo = GraphRepository(conn)
    try:
        with repo.batch():
            repo.create_run("rollback.example", 0, 1)
            raise RuntimeError("stop")
    except RuntimeError:
        pass
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_markdown_report_explains_claim_policy_and_evidence() -> None:
    repo, run_id = _repo_with_run()
    observation = Observation.build(
        subject_type="domain", subject_value_norm="example.com", predicate="has_subdomain",
        object_type="domain", object_value_norm="api.example.com", source="crtsh",
        confidence=0.9, idempotency_key="ct-1",
    )
    observation_id = repo.add_observation(run_id, observation)
    projected = project_domain_relationship(
        relation_type="domain_has_subdomain", subject_domain="example.com",
        object_domain="api.example.com", score=0.9, source="crtsh",
    )
    claim_id = repo.add_claim(run_id, projected.claim)
    repo.link_claim_evidence(
        claim_id, observation_id, "supports", projected.evidence_weight, projected.evidence_reason
    )
    report = render_markdown_report(repo.get_run_graph(run_id))
    assert "## Relationship Claims" in report
    assert "`example.com` -> `api.example.com`" in report
    assert "policy `relationship-v1`" in report
    assert "supports via `crtsh`" in report


def test_cached_run_rebuilds_dns_observations_and_claim_projection() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    repo = GraphRepository(conn)
    settings = Settings.from_env()
    settings.map_subdomains = False
    settings.cache_ttl_hours = 168
    orchestrator = RunOrchestrator(
        repository=repo, whois_provider=_Whois(), basic_info_provider=_Basic(),
        reverse_whois_provider=_NoSearch(), crtsh_provider=_NoSearch(),
        hackertarget_provider=_NoSearch(), dns_provider=_DNS(),
        relationship_engine=_NoPivots(), settings=settings,
    )
    first = asyncio.run(orchestrator.run("example.com", max_depth=0))
    second = asyncio.run(orchestrator.run("example.com", max_depth=0))
    first_graph = repo.get_run_graph(first.run_id)
    second_graph = repo.get_run_graph(second.run_id)
    assert second_graph["claim_projection"] == first_graph["claim_projection"]
    assert {
        (row["source"], row["predicate"], row["object_value_norm"])
        for row in second_graph["observations"]
    } == {
        (row["source"], row["predicate"], row["object_value_norm"])
        for row in first_graph["observations"]
    }


def test_pivot_claim_links_back_to_underlying_whois_observation() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    repo = GraphRepository(conn)
    settings = Settings.from_env()
    settings.map_subdomains = False
    settings.cache_ttl_hours = 0
    orchestrator = RunOrchestrator(
        repository=repo, whois_provider=_Whois(), basic_info_provider=_Basic(),
        reverse_whois_provider=_NoSearch(), crtsh_provider=_NoSearch(),
        hackertarget_provider=_NoSearch(), dns_provider=_DNS(),
        relationship_engine=_EmailPivot(), settings=settings,
    )
    summary = asyncio.run(orchestrator.run("example.com", max_depth=0))
    pivot_claim = next(
        claim for claim in repo.get_claims_with_evidence(summary.run_id)
        if claim["claim_type"] == "domain_has_identifier"
    )
    assert pivot_claim["object_value_norm"] == "security@example.com"
    assert pivot_claim["evidence"][0]["source"] == "test-whois"
    assert pivot_claim["evidence"][0]["predicate"] == "has_registrant_email"
    graph = repo.get_run_graph(summary.run_id)
    decision = graph["pivot_decisions"][0]
    assert decision["evidence_gap"] == "asset_discovery"
    assert decision["utility"] == 0.9
    assert decision["estimated_logical_calls"] == 1
    assert decision["policy_version"] == "pivot-utility-v1"
    report = render_markdown_report(graph)
    assert "gap `asset_discovery`" in report
    assert "policy `pivot-utility-v1`" in report


def test_provider_failure_produces_degraded_partial_run_with_usage_record() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    repo = GraphRepository(conn)
    settings = Settings.from_env()
    settings.map_subdomains = False
    settings.cache_ttl_hours = 0
    settings.retry_count = 0
    orchestrator = RunOrchestrator(
        repository=repo, whois_provider=_WhoisFails(), basic_info_provider=_Basic(),
        reverse_whois_provider=_NoSearch(), crtsh_provider=_NoSearch(),
        hackertarget_provider=_NoSearch(), dns_provider=_DNS(),
        relationship_engine=_NoPivots(), settings=settings,
    )
    summary = asyncio.run(orchestrator.run("example.com", max_depth=0))
    assert summary.status == "completed_degraded"
    graph = repo.get_run_graph(summary.run_id)
    whois_usage = next(row for row in graph["provider_usage"] if row["capability"] == "whois")
    assert whois_usage["status"] == "error"
    assert any(row["predicate"] == "resolves_to" for row in graph["observations"])


def test_all_disabled_data_providers_degrade_cleanly_without_calls() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    repo = GraphRepository(conn)
    settings = Settings.from_env()
    settings.map_subdomains = True
    settings.cache_ttl_hours = 0
    orchestrator = RunOrchestrator(
        repository=repo, whois_provider=None, basic_info_provider=None,
        reverse_whois_provider=None, crtsh_provider=None,
        hackertarget_provider=None, dns_provider=None,
        relationship_engine=_NoPivots(), settings=settings,
    )
    summary = asyncio.run(orchestrator.run("example.com", max_depth=0))
    graph = repo.get_run_graph(summary.run_id)
    assert summary.status == "completed"
    assert graph["provider_usage"] == []
    assert graph["observations"] == []
    assert graph["task_summary"]["succeeded"] == 1


def _repo_with_run() -> tuple[GraphRepository, str]:
    conn = get_connection(":memory:")
    init_db(conn)
    repo = GraphRepository(conn)
    return repo, repo.create_run("example.com", 0, 1)
