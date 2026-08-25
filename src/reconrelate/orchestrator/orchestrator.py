from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from reconrelate.llm_orchestration.relationship_engine import RelationshipEngine
from reconrelate.llm_orchestration.model_pricing import PRICE_CATALOG_VERSION
from reconrelate.llm_orchestration.response_parser import is_noise_domain
from reconrelate.config.settings import Settings
from reconrelate.core.normalize import normalize_domain, normalize_identifier, registrable_domain
from reconrelate.core.evidence import Observation
from reconrelate.core.claim_projection import project_relationship
from reconrelate.core.provider_result import (
    ProviderResult,
    observations_from_result,
    provider_identity,
    provider_is_billable,
    provider_concurrency_limit,
    provider_rate_limit,
    provider_response_limit,
    provider_result_limit,
    provider_request_limit,
    provider_page_limit,
    provider_timeout,
    subdomain_values,
)
from reconrelate.core.provider_execution import ExecutionBudget, ProviderExecutor
from reconrelate.core.provider_data_policy import observation_policy_fields, provider_data_policy
from reconrelate.core.query_optimizer import score_pivot
from reconrelate.core.resilience import waterfall
from reconrelate.core.types import Identifier, PivotCandidate, RunSummary, TrackerVerification
from reconrelate.core.types import WhoisRecord
from reconrelate.data_gathering.basic_info_provider import BasicInfoProvider
from reconrelate.data_gathering.crtsh_provider import CrtshProvider
from reconrelate.data_gathering.dns_provider import DNSProvider
from reconrelate.data_gathering.hackertarget_provider import HackerTargetProvider
from reconrelate.data_gathering.reverse_whois_provider import ReverseWhoisProvider
from reconrelate.data_gathering.whois_provider import WhoisProvider
from reconrelate.core.errors import SecurityError
from reconrelate.db.repositories import GraphRepository
from reconrelate.security.safe_target import validate_scan_target

logger = logging.getLogger(__name__)


def _registration_identity_sufficient(record: WhoisRecord) -> bool:
    return any((record.registrant_org, record.registrant_name,
                record.registrant_email, record.registrant_phone))


def _merge_registration_records(domain: str, records: list[WhoisRecord]) -> WhoisRecord:
    def first(field: str) -> str:
        return next((str(getattr(record, field)) for record in records if getattr(record, field)), "")

    sources = [str(record.raw.get("source", "unknown")) for record in records]
    return WhoisRecord(
        domain=domain,
        registrant_name=first("registrant_name"),
        registrant_org=first("registrant_org"),
        registrant_email=first("registrant_email"),
        registrant_phone=first("registrant_phone"),
        nameservers=sorted({value for record in records for value in record.nameservers}),
        creation_date=first("creation_date"),
        expiration_date=first("expiration_date"),
        raw={"source": "registration-cascade", "sources": sources},
    )


def _cache_is_fresh(last_scraped: str, ttl_hours: int) -> bool:
    """True if a cache entry scraped at `last_scraped` is still within the TTL window."""
    if ttl_hours <= 0:
        return False
    try:
        ts = datetime.fromisoformat(last_scraped)
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - ts <= timedelta(hours=ttl_hours)


@dataclass(slots=True)
class DomainWorkItem:
    domain: str
    depth: int
    parent_domain_node_id: str | None
    task_id: str | None = None


class DomainQueue:
    def __init__(
        self,
        repository: GraphRepository | None = None,
        run_id: str | None = None,
        lease_seconds: int = 120,
    ) -> None:
        self._queue: deque[DomainWorkItem] = deque()
        self.repository = repository
        self.run_id = run_id
        self.lease_seconds = lease_seconds

    def push(self, item: DomainWorkItem) -> None:
        if self.repository is not None and self.run_id is not None:
            self.repository.enqueue_run_task(
                self.run_id,
                task_type="map_domain",
                idempotency_key=f"map_domain:{item.depth}:{item.domain}",
                payload={
                    "domain": item.domain,
                    "depth": item.depth,
                    "parent_domain_node_id": item.parent_domain_node_id,
                },
            )
            return
        self._queue.append(item)

    def pop(self) -> DomainWorkItem:
        if self.repository is not None and self.run_id is not None:
            task = self.repository.claim_run_task(self.run_id, lease_seconds=self.lease_seconds)
            if task is None:
                raise IndexError("no runnable domain task")
            payload = task["payload"]
            return DomainWorkItem(
                domain=str(payload["domain"]),
                depth=int(payload["depth"]),
                parent_domain_node_id=(
                    str(payload["parent_domain_node_id"])
                    if payload.get("parent_domain_node_id") else None
                ),
                task_id=str(task["id"]),
            )
        return self._queue.popleft()

    def __bool__(self) -> bool:
        if self.repository is not None and self.run_id is not None:
            return self.repository.count_runnable_tasks(self.run_id) > 0
        return bool(self._queue)

    def __len__(self) -> int:
        if self.repository is not None and self.run_id is not None:
            return self.repository.count_runnable_tasks(self.run_id)
        return len(self._queue)


class RunOrchestrator:
    def __init__(
        self,
        repository: GraphRepository,
        whois_provider: WhoisProvider | list[object],
        basic_info_provider: BasicInfoProvider,
        reverse_whois_provider: ReverseWhoisProvider,
        crtsh_provider: CrtshProvider,
        hackertarget_provider: HackerTargetProvider,
        dns_provider: DNSProvider,
        relationship_engine: RelationshipEngine,
        settings: Settings,
        acquisitions_provider=None,
        subfinder_provider=None,
        historical_web_provider=None,
    ) -> None:
        self.repository = repository
        self.whois_providers = (
            list(whois_provider) if isinstance(whois_provider, (list, tuple))
            else ([whois_provider] if whois_provider is not None else [])
        )
        self.whois_provider = self.whois_providers[0] if self.whois_providers else None
        self.basic_info_provider = basic_info_provider
        self.reverse_whois_provider = reverse_whois_provider
        self.crtsh_provider = crtsh_provider
        self.hackertarget_provider = hackertarget_provider
        self.dns_provider = dns_provider
        self.relationship_engine = relationship_engine
        self.settings = settings
        # Optional org→org acquisition expansion (off unless settings.expand_acquisitions).
        self.acquisitions_providers = (
            list(acquisitions_provider) if isinstance(acquisitions_provider, (list, tuple))
            else ([acquisitions_provider] if acquisitions_provider is not None else [])
        )
        self.acquisitions_provider = (
            self.acquisitions_providers[0] if self.acquisitions_providers else None
        )
        self.subfinder_provider = subfinder_provider
        self.historical_web_provider = historical_web_provider
        policy_providers = [
            *self.whois_providers, basic_info_provider, reverse_whois_provider,
            crtsh_provider, hackertarget_provider, dns_provider, subfinder_provider,
            historical_web_provider,
        ]
        if getattr(settings, "expand_acquisitions", False):
            policy_providers.extend(self.acquisitions_providers)
        self.cross_run_cache_allowed = all(
            provider_data_policy(provider).cross_run_cache
            for provider in policy_providers if provider is not None
        )
        self._acq_expanded: set[tuple[str, str]] = set()
        # GLEIF and SEC report real org->org relations but never a domain (no equivalent of
        # Wikidata's P856 official-website claim) — resolved via a Wikidata name lookup fallback
        # in _resolve_org_domain_via_wikidata, cached per run since the same org name can recur
        # across providers and domains.
        self._wikidata_domain_cache: dict[str, str] = {}
        self.execution_budget = ExecutionBudget(
            max_calls=self.settings.max_provider_calls,
            max_billable_units=self.settings.max_billable_units,
        )
        self.provider_executor = ProviderExecutor(
            timeout_sec=self.settings.request_timeout_sec,
            retry_count=self.settings.retry_count,
            telemetry_sink=getattr(self.repository, "record_provider_call", None),
            shared_circuit_check=getattr(self.repository, "provider_circuit_is_open", None),
            shared_success_sink=getattr(self.repository, "record_provider_success", None),
            shared_failure_sink=(
                lambda provider, error, threshold, cooldown: self.repository.record_provider_failure(
                    provider,
                    error,
                    threshold=threshold,
                    cooldown_seconds=cooldown,
                )
                if hasattr(self.repository, "record_provider_failure") else None
            ),
            permit_acquire=(
                lambda provider, owner, rate, concurrency, lease, request_id: self.repository.acquire_provider_permit(
                    provider,
                    owner=owner,
                    rate_limit_per_minute=rate,
                    concurrency_limit=concurrency,
                    lease_seconds=lease,
                    request_id=request_id,
                    waiter_ttl_seconds=max(10.0, lease * 2),
                )
                if hasattr(self.repository, "acquire_provider_permit") else ""
            ),
            permit_release=getattr(self.repository, "release_provider_permit", None),
            permit_cancel=getattr(self.repository, "cancel_provider_waiter", None),
            capacity_wait_sec=self.settings.provider_capacity_wait_sec,
            execution_budget=self.execution_budget,
        )

    def _add_relationship_claim(
        self,
        *,
        run_id: str,
        relation_type: str,
        subject_domain: str,
        object_domain: str,
        score: float,
        source: str,
        observation_id: str,
        subject_type: str = "domain",
        object_type: str = "domain",
    ) -> str:
        projected = project_relationship(
            relation_type=relation_type,
            subject_type=subject_type,
            subject_value=subject_domain,
            object_type=object_type,
            object_value=object_domain,
            score=score,
            source=source,
        )
        claim_id = self.repository.add_claim(run_id, projected.claim)
        self.repository.link_claim_evidence(
            claim_id,
            observation_id,
            "supports",
            projected.evidence_weight,
            projected.evidence_reason,
        )
        return claim_id

    def _add_infrastructure_from_observation(
        self,
        *,
        run_id: str,
        domain_node_id: str,
        depth: int,
        observation_id: str,
        observation: Observation,
    ) -> None:
        if observation.predicate == "redirects_to_domain" and observation.object_value_norm:
            try:
                target = registrable_domain(normalize_domain(observation.object_value_norm))
                validate_scan_target(target)
            except Exception:
                return
            target_id = self.repository.get_or_create_node(
                run_id=run_id, node_type="domain", value_norm=target,
                metadata={"discovered_by": "http_redirect", "not_auto_enqueued": True},
            )
            relation_type = "domain_redirects_to"
            self.repository.add_edge(
                run_id, domain_node_id, target_id, relation_type, depth,
                observation.source, observation.confidence,
            )
            self._add_relationship_claim(
                run_id=run_id, relation_type=relation_type,
                subject_domain=observation.subject_value_norm, object_domain=target,
                score=observation.confidence, source=observation.source,
                observation_id=observation_id,
            )
            return
        if observation.normalized.get("capability") != "dns" or not observation.object_value_norm:
            return
        mapping = {
            "resolves_to": ("ip", "domain_has_ip"),
            "has_mx": ("mx", "domain_has_mx"),
            "has_nameserver": ("ns", "domain_has_ns"),
            "has_cname": ("domain", "domain_has_cname"),
        }
        mapped = mapping.get(observation.predicate)
        if mapped is None:
            return
        node_type, relation_type = mapped
        metadata = None
        if node_type == "ip":
            metadata = {"ip_version": "v6" if ":" in observation.object_value_norm else "v4"}
        target_id = self.repository.get_or_create_node(
            run_id=run_id,
            node_type=node_type,
            value_norm=observation.object_value_norm,
            metadata=metadata,
        )
        self.repository.add_edge(
            run_id,
            domain_node_id,
            target_id,
            relation_type,
            depth,
            observation.source,
            observation.confidence,
        )
        self._add_relationship_claim(
            run_id=run_id,
            relation_type=relation_type,
            subject_domain=observation.subject_value_norm,
            object_domain=observation.object_value_norm,
            score=observation.confidence,
            source=observation.source,
            observation_id=observation_id,
            object_type=node_type,
        )

    def _replay_cached_observations(
        self,
        run_id: str,
        domain_node_id: str,
        depth: int,
        serialized: list[dict],
    ) -> None:
        for value in serialized:
            if not isinstance(value, dict):
                continue
            try:
                observation = Observation.from_dict(value)
            except (KeyError, TypeError, ValueError):
                continue
            observation_id = self.repository.add_observation(run_id, observation)
            self._add_infrastructure_from_observation(
                run_id=run_id,
                domain_node_id=domain_node_id,
                depth=depth,
                observation_id=observation_id,
                observation=observation,
            )

    async def _expand_acquisitions(
        self, pivots, run_id, domain_node_id, work_item, queue, enqueued, depth_cap, collected,
    ) -> None:
        """Run all selected corporate sources independently and preserve their provenance."""
        if not getattr(self.settings, "expand_acquisitions", False):
            return
        for provider in self.acquisitions_providers:
            await self._expand_acquisitions_for_provider(
                provider, pivots, run_id, domain_node_id, work_item, queue, enqueued,
                depth_cap, collected,
            )

    async def _resolve_org_domain_via_wikidata(self, run_id: str, org: str) -> str:
        """Fallback for a provider (GLEIF, SEC) that names a related org but has no domain field.

        Wikidata is the only configured acquisitions source with a real org->domain link
        (P856 official website); GLEIF's LEI hierarchy and SEC's 8-K text extraction both
        report the *organization*, correctly, but have no equivalent of that claim, so their
        domain field is always "". Rather than leave a well-sourced, filing-backed relation
        (SEC) or LEI-verified relation (GLEIF) permanently unreachable in the graph, resolve the
        org name through the same Wikidata path used for org pivots generally. Skipped when the
        current provider already is wikidata: it already tried its own most direct route
        (property traversal from the source entity) and a second name-based search on its own
        miss is unlikely to add anything.
        """
        org = org.strip()
        if not org:
            return ""
        if org in self._wikidata_domain_cache:
            return self._wikidata_domain_cache[org]
        wikidata = next(
            (p for p in self.acquisitions_providers if provider_identity(p, "") == "wikidata"), None
        )
        if wikidata is None:
            return ""
        try:
            domain = await self.provider_executor.execute(
                run_id=run_id,
                provider="wikidata",
                capability="acquisitions",
                operation="resolve_domain",
                call=lambda: wikidata.resolve_domain(org),
                validator=lambda value: isinstance(value, str),
                billable=provider_is_billable(wikidata),
                concurrency_limit=provider_concurrency_limit(wikidata),
                rate_limit_per_minute=provider_rate_limit(wikidata),
                max_response_bytes=provider_response_limit(wikidata),
                max_result_items=provider_result_limit(wikidata),
                max_requests_per_attempt=provider_request_limit(wikidata),
                max_pages_per_attempt=provider_page_limit(wikidata),
                timeout_sec=provider_timeout(wikidata, self.settings.request_timeout_sec),
            )
        except Exception as exc:
            logger.info("Wikidata domain fallback failed for org %r: %s", org, exc)
            domain = ""
        self._wikidata_domain_cache[org] = domain
        return domain

    async def _expand_acquisitions_for_provider(
        self, provider, pivots, run_id, domain_node_id, work_item, queue, enqueued, depth_cap,
        collected,
    ) -> None:
        """Resolve org pivots to acquired/related-company DOMAINS via Wikidata official-website
        (P856) — reliable, no text search. Adds a labeled acquisition edge to the real domain and
        enqueues it for further mapping."""
        if provider is None:
            return
        added = 0
        source = provider_identity(provider, "unknown")
        for p in [p for p in pivots
                  if p.id_type == "org" and (source, p.value) not in self._acq_expanded]:
            self._acq_expanded.add((source, p.value))
            try:
                name = source
                related = await self.provider_executor.execute(
                    run_id=run_id,
                    provider=name,
                    capability="acquisitions",
                    operation="related_orgs",
                    call=lambda: provider.related_orgs(p.value, max_results=8),
                    validator=lambda value: isinstance(value, list) and all(isinstance(item, dict) for item in value),
                    billable=provider_is_billable(provider),
                    concurrency_limit=provider_concurrency_limit(provider),
                    rate_limit_per_minute=provider_rate_limit(provider),
                    max_response_bytes=provider_response_limit(provider),
                    max_result_items=provider_result_limit(provider),
                    max_requests_per_attempt=provider_request_limit(provider),
                    max_pages_per_attempt=provider_page_limit(provider),
                    timeout_sec=provider_timeout(
                        provider, self.settings.request_timeout_sec
                    ),
                )
            except Exception as e:
                # Non-fatal enrichment failure (e.g. a provider circuit opening under rate limit);
                # the run continues and reports completed_degraded. Info, not a terminal warning.
                logger.info("acquisitions expansion failed for %s: %s", p.value, e)
                continue

            # Resolve GLEIF/SEC's domain-less relations before opening the batch below: batch()
            # holds a single deferred-commit SQLite transaction open on the shared connection
            # until it exits, so an await (a real network call, here) inside that block would
            # hold the transaction open across it — resolve everything network-bound first, then
            # write everything synchronously.
            fallback_domains: dict[str, str] = {}
            if source != "wikidata":
                for rel in related:
                    org = str(rel.get("org", "")).strip()
                    if not str(rel.get("domain", "")).strip() and org and org not in fallback_domains:
                        fallback_domains[org] = await self._resolve_org_domain_via_wikidata(run_id, org)

            with self.repository.batch():
                for rel in related:
                    relation = str(rel.get("relation", "related"))
                    domain = str(rel.get("domain", "")).strip()
                    domain_resolved_via_wikidata = False
                    if not domain and str(rel.get("org", "")).strip():
                        org = str(rel.get("org", "")).strip()
                        self.repository.add_observation(run_id, Observation.build(
                            subject_type="organization",
                            subject_value_norm=p.value,
                            predicate=relation,
                            object_type="organization",
                            object_value_norm=org,
                            source=source,
                            source_record_id=(str(rel.get("source_record_id", "")) or
                                              str(rel.get("lei", "")) or None),
                            confidence=0.95 if source in {"gleif", "sec-edgar"} else 0.8,
                            normalized={
                                "related_organization": org,
                                "relation": relation,
                                "lei": str(rel.get("lei", "")),
                                "subject_lei": str(rel.get("subject_lei", "")),
                                "cik": str(rel.get("cik", "")),
                                "filing_date": str(rel.get("filing_date", "")),
                                "filing_url": str(rel.get("filing_url", "")),
                                "supporting_text": str(rel.get("supporting_text", ""))[:500],
                            },
                            **observation_policy_fields(provider),
                        ))

                        # Already resolved above, outside the batch's open transaction. GLEIF and
                        # SEC name the related org correctly but have no domain field at all —
                        # without this, a well-sourced, LEI-verified or filing-backed relation
                        # would permanently dead-end here and never reach the graph.
                        domain = fallback_domains.get(org, "")
                        domain_resolved_via_wikidata = bool(domain)
                    if not domain:
                        continue  # no official website on Wikidata (direct or fallback) → skip
                    try:
                        domain = registrable_domain(normalize_domain(domain))
                        validate_scan_target(domain)
                    except SecurityError:
                        continue
                    except Exception:
                        continue
                    if is_noise_domain(domain) or domain == work_item.domain:
                        continue

                    acquisition_observation = Observation.build(
                        subject_type="organization",
                        subject_value_norm=p.value,
                        predicate=relation,
                        object_type="domain",
                        object_value_norm=domain,
                        source=source,
                        source_record_id=(str(rel.get("source_record_id", "")) or
                                          str(rel.get("qid", "")) or None),
                        # Slightly lower than a source's own direct domain claim (0.9): resolved
                        # by a secondary name lookup against Wikidata, not reported by source
                        # itself. Still an exact P856 official-website match, not a text guess.
                        confidence=0.75 if domain_resolved_via_wikidata else 0.9,
                        normalized={
                            "related_organization": str(rel.get("org", "")),
                            "relation": relation,
                            "official_domain": domain,
                            **({"domain_resolved_via": "wikidata_name_lookup"}
                               if domain_resolved_via_wikidata else {}),
                        },
                        **observation_policy_fields(provider),
                    )
                    acquisition_observation_id = self.repository.add_observation(run_id, acquisition_observation)
                    relation_type = f"acquisition_{relation}"
                    self._add_relationship_claim(
                        run_id=run_id,
                        relation_type=relation_type,
                        subject_domain=work_item.domain,
                        object_domain=domain,
                        score=0.9,
                        source=acquisition_observation.source,
                        observation_id=acquisition_observation_id,
                    )
                    child_id = self.repository.get_or_create_node(
                        run_id=run_id, node_type="domain", value_norm=domain,
                        metadata={"discovered_by": f"{source}_corporate_relation", "relation": relation,
                                  "org": str(rel.get("org", ""))},
                    )
                    self.repository.add_edge(
                        run_id, domain_node_id, child_id, relation_type,
                        work_item.depth + 1, source, 0.9,
                    )
                    self.repository.add_lineage(run_id, child_id, domain_node_id, work_item.depth + 1)
                    added += 1
                    if collected is not None and provider_data_policy(provider).cross_run_cache:
                        collected.append({
                            "domain": domain,
                            "source": acquisition_observation.source,
                            "confidence": 0.9,
                            "id_type": "org",
                            "id_value": p.value,
                            "relation_type": relation_type,
                            "observation": acquisition_observation.to_dict(),
                        })

                    if depth_cap is not None and work_item.depth + 1 > depth_cap:
                        continue
                    key = (domain, work_item.depth + 1)
                    if key in enqueued or self.repository.is_domain_processed(run_id, child_id):
                        continue
                    cap_q = self.settings.max_pending_queue
                    if cap_q and len(queue) >= cap_q:
                        continue
                    queue.push(DomainWorkItem(domain=domain, depth=work_item.depth + 1,
                                              parent_domain_node_id=domain_node_id))
                    enqueued.add(key)
        if added:
            logger.info("corporate relationships: added %d official domains via %s", added, source)

    # ── Concurrent data-gathering helpers ──────────────────────────────

    async def _fetch_subdomains(self, domain: str, run_id: str) -> ProviderResult[list]:
        """Run subdomain enumeration through the waterfall asynchronously."""
        n = self.settings.max_subdomains_fetched
        builtins = (
            [self.hackertarget_provider, self.crtsh_provider]
            if self.settings.prefer_fast_subdomain_source
            else [self.crtsh_provider, self.hackertarget_provider]
        )
        sources = ([self.subfinder_provider] if self.subfinder_provider is not None else []) + builtins

        for provider in sources:
            if provider is None:
                continue
            name = provider_identity(provider, provider.__class__.__name__.lower())
            try:
                res = await self.provider_executor.execute(
                    run_id=run_id,
                    provider=name,
                    capability="subdomains",
                    operation="search",
                    call=lambda provider=provider: provider.search(domain, max_results=n),
                    validator=lambda value: isinstance(value, list) and all(
                        isinstance(item, str) or hasattr(item, "domain") for item in value
                    ),
                    billable=provider_is_billable(provider),
                    concurrency_limit=provider_concurrency_limit(provider),
                    rate_limit_per_minute=provider_rate_limit(provider),
                    max_response_bytes=provider_response_limit(provider),
                    max_result_items=provider_result_limit(provider),
                    max_requests_per_attempt=provider_request_limit(provider),
                    max_pages_per_attempt=provider_page_limit(provider),
                    timeout_sec=provider_timeout(provider, self.settings.request_timeout_sec),
                )
                if res:
                    return ProviderResult.from_data(name, "subdomains", res, subject=domain)
            except Exception as e:
                logger.warning("Subdomain provider %s failed for %s: %s", name, domain, e)
        return ProviderResult.from_data("subdomain_waterfall", "subdomains", [], subject=domain)

    async def _gather_all_data(self, domain: str, do_subdomain_enum: bool, run_id: str):
        """
        Run WHOIS, Basic Info, DNS, and (optionally) Subdomain Enumeration
        concurrently using asyncio.gather().
        """
        async def _safe_whois():
            if not self.whois_providers:
                disabled = ProviderResult.from_data("disabled", "whois", None, subject=domain)
                return disabled, []
            evidence: list[ProviderResult] = []
            records: list[WhoisRecord] = []
            last_error: Exception | None = None
            for provider in self.whois_providers:
                name = provider_identity(provider, "python-whois")
                try:
                    data = await self.provider_executor.execute(
                        run_id=run_id, provider=name, capability="whois", operation="lookup",
                        call=lambda provider=provider: provider.lookup(domain),
                        validator=lambda value: isinstance(value, WhoisRecord),
                        billable=provider_is_billable(provider),
                        concurrency_limit=provider_concurrency_limit(provider),
                        rate_limit_per_minute=provider_rate_limit(provider),
                        max_response_bytes=provider_response_limit(provider),
                        max_result_items=provider_result_limit(provider),
                        max_requests_per_attempt=provider_request_limit(provider),
                        max_pages_per_attempt=provider_page_limit(provider),
                        timeout_sec=provider_timeout(provider, self.settings.request_timeout_sec),
                    )
                    result = ProviderResult.from_data(name, "whois", data, subject=domain)
                    evidence.append(result)
                    records.append(data)
                    if _registration_identity_sufficient(data):
                        break
                except Exception as exc:
                    last_error = exc
                    logger.warning("Registration provider %s failed for %s: %s", name, domain, exc)
                    evidence.append(ProviderResult.failure(name, "whois", exc, subject=domain))
            if records:
                merged = _merge_registration_records(domain, records)
                return ProviderResult.from_data(
                    "registration-cascade", "whois", merged, subject=domain
                ), evidence
            failure = ProviderResult.failure(
                "registration-cascade", "whois",
                last_error or RuntimeError("all registration providers unavailable"),
                subject=domain,
            )
            return failure, evidence

        async def _safe_basic():
            if self.basic_info_provider is None:
                return ProviderResult.from_data("disabled", "basic_info", None, subject=domain)
            try:
                name = provider_identity(self.basic_info_provider, "http-html")
                data = await self.provider_executor.execute(
                    run_id=run_id, provider=name, capability="basic_info", operation="lookup",
                    call=lambda: self.basic_info_provider.lookup(domain),
                    validator=lambda value: hasattr(value, "domain") and hasattr(value, "raw"),
                    billable=provider_is_billable(self.basic_info_provider),
                    concurrency_limit=provider_concurrency_limit(self.basic_info_provider),
                    rate_limit_per_minute=provider_rate_limit(self.basic_info_provider),
                    max_response_bytes=provider_response_limit(self.basic_info_provider),
                    max_result_items=provider_result_limit(self.basic_info_provider),
                    max_requests_per_attempt=provider_request_limit(self.basic_info_provider),
                    max_pages_per_attempt=provider_page_limit(self.basic_info_provider),
                    timeout_sec=provider_timeout(
                        self.basic_info_provider, self.settings.request_timeout_sec
                    ),
                )
                return ProviderResult.from_data(
                    provider_identity(self.basic_info_provider, "http-html"), "basic_info", data, subject=domain
                )
            except Exception as exc:
                logger.warning("Concurrent basic info failed for %s: %s", domain, exc)
                return ProviderResult.failure("http-html", "basic_info", exc, subject=domain)

        async def _safe_dns():
            if self.dns_provider is None:
                return ProviderResult.from_data("disabled", "dns", None, subject=domain)
            try:
                name = provider_identity(self.dns_provider, "system-dns")
                data = await self.provider_executor.execute(
                    run_id=run_id, provider=name, capability="dns", operation="lookup",
                    call=lambda: self.dns_provider.lookup(domain),
                    validator=lambda value: hasattr(value, "domain") and hasattr(value, "a_records"),
                    billable=provider_is_billable(self.dns_provider),
                    concurrency_limit=provider_concurrency_limit(self.dns_provider),
                    rate_limit_per_minute=provider_rate_limit(self.dns_provider),
                    max_response_bytes=provider_response_limit(self.dns_provider),
                    max_result_items=provider_result_limit(self.dns_provider),
                    max_requests_per_attempt=provider_request_limit(self.dns_provider),
                    max_pages_per_attempt=provider_page_limit(self.dns_provider),
                    timeout_sec=provider_timeout(self.dns_provider, self.settings.request_timeout_sec),
                )
                return ProviderResult.from_data(
                    provider_identity(self.dns_provider, "system-dns"), "dns", data, subject=domain
                )
            except Exception as exc:
                logger.warning("Concurrent dns failed for %s: %s", domain, exc)
                return ProviderResult.failure("system-dns", "dns", exc, subject=domain)

        async def _safe_subdomains():
            if not do_subdomain_enum:
                return ProviderResult.from_data("disabled", "subdomains", [], subject=domain)
            try:
                return await self._fetch_subdomains(domain, run_id)
            except Exception as exc:
                logger.warning("Concurrent subdomains failed for %s: %s", domain, exc)
                return ProviderResult.failure("subdomain_waterfall", "subdomains", exc, subject=domain)

        async def _safe_history():
            if not self.settings.historical_web or self.historical_web_provider is None:
                return ProviderResult.from_data("disabled", "historical_web", [], subject=domain)
            provider = self.historical_web_provider
            name = provider_identity(provider, "wayback")
            try:
                data = await self.provider_executor.execute(
                    run_id=run_id, provider=name, capability="historical_web", operation="lookup",
                    call=lambda: provider.lookup(domain, max_results=4),
                    validator=lambda value: isinstance(value, list) and all(
                        hasattr(item, "captured_at") and hasattr(item, "archive_url") for item in value
                    ),
                    billable=provider_is_billable(provider),
                    concurrency_limit=provider_concurrency_limit(provider),
                    rate_limit_per_minute=provider_rate_limit(provider),
                    max_response_bytes=provider_response_limit(provider),
                    max_result_items=provider_result_limit(provider),
                    max_requests_per_attempt=provider_request_limit(provider),
                    max_pages_per_attempt=provider_page_limit(provider),
                    timeout_sec=provider_timeout(provider, self.settings.request_timeout_sec),
                )
                return ProviderResult.from_data(name, "historical_web", data, subject=domain)
            except Exception as exc:
                logger.warning("Historical web provider %s failed for %s: %s", name, domain, exc)
                return ProviderResult.failure(name, "historical_web", exc, subject=domain)

        whois_bundle, basic_result, dns_result, subdomains, history_result = await asyncio.gather(
            _safe_whois(),
            _safe_basic(),
            _safe_dns(),
            _safe_subdomains(),
            _safe_history(),
        )

        whois_result, registration_evidence = whois_bundle
        return whois_result, registration_evidence, basic_result, dns_result, subdomains, history_result

    async def _reverse_whois_batch(
        self,
        pivots,
        run_id: str,
        domain_node_id: str,
        work_item: DomainWorkItem,
        queue: DomainQueue,
        enqueued: set,
        depth_cap: int | None,
        collected: list[dict] | None = None,
    ):
        """Fire all reverse-WHOIS pivot searches concurrently using asyncio.gather()."""
        if not pivots:
            return

        pivot_contexts = []
        for pivot in pivots:
            planner_decision = score_pivot(
                pivot,
                tracker_verification_candidates=self.settings.max_domains_per_identifier,
            )
            with self.repository.batch():
                evidence_rows = self.repository.find_observations_for_object(
                    run_id,
                    subject_value_norm=work_item.domain,
                    object_value_norm=pivot.value,
                )
                if evidence_rows:
                    first_evidence = evidence_rows[0]
                    evidence_id = str(first_evidence["id"])
                    evidence_source = str(first_evidence["source"])
                else:
                    derived = Observation.build(
                        subject_type="domain",
                        subject_value_norm=work_item.domain,
                        predicate="selected_pivot",
                        object_type="identifier",
                        object_value_norm=pivot.value,
                        source="relationship_engine",
                        confidence=pivot.score,
                        normalized={"identifier_type": pivot.id_type, "reason": pivot.reason},
                    )
                    evidence_id = self.repository.add_observation(run_id, derived)
                    evidence_source = derived.source
                pivot_claim_id = self._add_relationship_claim(
                    run_id=run_id,
                    relation_type="domain_has_identifier",
                    subject_domain=work_item.domain,
                    object_domain=pivot.value,
                    score=pivot.score,
                    source=evidence_source,
                    observation_id=evidence_id,
                    object_type="identifier",
                )
                for extra in evidence_rows[1:]:
                    self.repository.link_claim_evidence(
                        pivot_claim_id,
                        str(extra["id"]),
                        "supports",
                        min(max(float(extra["confidence"]), 0.0), 1.0),
                        f"{extra['source']} corroborates identifier selection",
                    )
                identifier_node_id = self.repository.get_or_create_node(
                    run_id=run_id,
                    node_type="identifier",
                    value_norm=pivot.value,
                    metadata={"identifier_type": pivot.id_type},
                )
                self.repository.add_edge(
                    run_id=run_id,
                    from_node_id=domain_node_id,
                    to_node_id=identifier_node_id,
                    relation_type="domain_has_identifier",
                    depth=work_item.depth,
                    source="relationship_engine",
                    confidence=pivot.score,
                )
                self.repository.add_edge(
                    run_id=run_id,
                    from_node_id=domain_node_id,
                    to_node_id=identifier_node_id,
                    relation_type="llm_selected_pivot",
                    depth=work_item.depth,
                    source="relationship_engine",
                    confidence=pivot.score,
                )
                self.repository.add_pivot_decision(
                    run_id=run_id,
                    domain_node_id=domain_node_id,
                    identifier_value_norm=pivot.value,
                    identifier_type=pivot.id_type,
                    score=pivot.score,
                    reason_short=pivot.reason,
                    evidence_gap=planner_decision.evidence_gap,
                    utility=planner_decision.utility,
                    estimated_logical_calls=planner_decision.estimated_logical_calls,
                    policy_version=planner_decision.policy_version,
                )
            pivot_contexts.append((pivot, identifier_node_id))

        if depth_cap is not None and work_item.depth >= depth_cap:
            return

        sem = asyncio.Semaphore(getattr(self.settings, "concurrency_pivot", 15))

        async def _do_search(pivot, identifier_node_id):
            async with sem:
                # Free text-search on an org/name string returns whatever pages mention it
                # (proven noise: "day one" → englishclub.com). Skip — org→domain is resolved
                # reliably via Wikidata P856 in _expand_acquisitions instead.
                #
                # ns pivots have the same failure mode, proven twice: a hostname like
                # "ns1.automattic.com" scores as a vanity nameserver (it contains the company's
                # own domain), but is commonly a CNAME to a shared third-party DNS provider, not
                # infrastructure the company operates. Reverse-searching that hostname as free
                # text returned ibm.com, cdc.gov, apollohospitals.com, waves.com (Automattic) and
                # microsoft.com, cbp.gov (Mozilla) — all unrelated. Whoxy's structured reverse
                # WHOIS doesn't even support an ns field (_FIELD_FOR has no "ns" key), so this
                # path was already a DuckDuckGo-only, free-text-only search for every ns pivot;
                # removing it costs no real capability, only the noise.
                if pivot.id_type in ("org", "name", "ns"):
                    return pivot, identifier_node_id, []
                if self.reverse_whois_provider is None:
                    return pivot, identifier_node_id, []
                try:
                    name = provider_identity(self.reverse_whois_provider, "reverse_whois")
                    res = await self.provider_executor.execute(
                        run_id=run_id,
                        provider=name,
                        capability="reverse_whois",
                        operation="search",
                        call=lambda: self.reverse_whois_provider.search(
                            identifier=Identifier(id_type=pivot.id_type, value=pivot.value),
                            max_results=self.settings.max_domains_per_identifier,
                        ),
                        validator=lambda value: isinstance(value, list) and all(
                            isinstance(item, str) for item in value
                        ),
                        billable=provider_is_billable(self.reverse_whois_provider),
                        concurrency_limit=provider_concurrency_limit(self.reverse_whois_provider),
                        rate_limit_per_minute=provider_rate_limit(self.reverse_whois_provider),
                        max_response_bytes=provider_response_limit(self.reverse_whois_provider),
                        max_result_items=provider_result_limit(self.reverse_whois_provider),
                        max_requests_per_attempt=provider_request_limit(self.reverse_whois_provider),
                        max_pages_per_attempt=provider_page_limit(self.reverse_whois_provider),
                        timeout_sec=provider_timeout(
                            self.reverse_whois_provider, self.settings.request_timeout_sec
                        ),
                    )
                    if pivot.id_type != "tracker":
                        return pivot, identifier_node_id, [(candidate, None) for candidate in res]

                    if self.basic_info_provider is None:
                        return pivot, identifier_node_id, []

                    async def _verify(candidate: str):
                        try:
                            normalized = registrable_domain(normalize_domain(candidate))
                            validate_scan_target(normalized)
                            provider = self.basic_info_provider
                            verifier_name = provider_identity(provider, "http-html")
                            verification = await self.provider_executor.execute(
                                run_id=run_id,
                                provider=verifier_name,
                                capability="verification",
                                operation="verify_tracker",
                                call=lambda: provider.verify_tracker(normalized, pivot.value),
                                validator=lambda value: isinstance(value, TrackerVerification),
                                billable=provider_is_billable(provider),
                                concurrency_limit=provider_concurrency_limit(provider),
                                rate_limit_per_minute=provider_rate_limit(provider),
                                max_response_bytes=provider_response_limit(provider),
                                max_result_items=provider_result_limit(provider),
                                max_requests_per_attempt=provider_request_limit(provider),
                                max_pages_per_attempt=provider_page_limit(provider),
                                timeout_sec=provider_timeout(provider, self.settings.request_timeout_sec),
                            )
                            return (normalized, verification) if verification.matched else None
                        except Exception as exc:
                            logger.info("Tracker verification rejected %s for %s: %s", candidate, pivot.value, exc)
                            return None

                    verified = await asyncio.gather(*[_verify(candidate) for candidate in res])
                    return pivot, identifier_node_id, [item for item in verified if item is not None]
                except Exception as exc:
                    logger.warning("Reverse WHOIS failed for %s=%s: %s", pivot.id_type, pivot.value, exc)
                    return pivot, identifier_node_id, []

        t_rw_all = time.perf_counter()
        results = await asyncio.gather(*[_do_search(p, node_id) for p, node_id in pivot_contexts])

        for pivot, identifier_node_id, related_domains in results:
            with self.repository.batch():
                for candidate, tracker_verification in related_domains:
                    try:
                        # Collapse to the registrable apex — we map related ROOT domains, so a
                        # subdomain of the target (www./in./ch.…) is not a new node, it's self.
                        normalized_candidate = registrable_domain(normalize_domain(candidate))
                        validate_scan_target(normalized_candidate)
                    except SecurityError:
                        continue
                    except Exception:
                        continue
                    if is_noise_domain(normalized_candidate) or normalized_candidate == work_item.domain:
                        continue

                    reverse_observation = Observation.build(
                            subject_type="identifier",
                            subject_value_norm=pivot.value,
                            predicate="links_domain",
                            object_type="domain",
                            object_value_norm=normalized_candidate,
                            source=provider_identity(self.reverse_whois_provider, "reverse_whois"),
                            confidence=pivot.score,
                            normalized={
                                "identifier_type": pivot.id_type,
                                "independently_verified": tracker_verification is not None,
                            },
                            **observation_policy_fields(self.reverse_whois_provider),
                        )
                    reverse_observation_id = self.repository.add_observation(
                        run_id, reverse_observation,
                    )
                    relationship_claim_id = self._add_relationship_claim(
                        run_id=run_id,
                        relation_type="related_domain_via_identifier",
                        subject_domain=work_item.domain,
                        object_domain=normalized_candidate,
                        score=pivot.score,
                        source=reverse_observation.source,
                        observation_id=reverse_observation_id,
                    )
                    verification_observation = None
                    if tracker_verification is not None:
                        verification_observation = Observation.build(
                            subject_type="domain",
                            subject_value_norm=normalized_candidate,
                            predicate="uses_tracker",
                            object_type="tracker",
                            object_value_norm=pivot.value,
                            source=provider_identity(self.basic_info_provider, "http-html"),
                            source_record_id=tracker_verification.final_url or normalized_candidate,
                            confidence=0.95,
                            normalized={
                                "verification": "exact_current_root_page",
                                "final_url": tracker_verification.final_url,
                            },
                        )
                        verification_observation_id = self.repository.add_observation(
                            run_id, verification_observation,
                        )
                        self.repository.link_claim_evidence(
                            relationship_claim_id,
                            verification_observation_id,
                            "supports",
                            0.95,
                            "exact tracker ID verified on candidate root page",
                        )

                    child_domain_node_id = self.repository.get_or_create_node(
                        run_id=run_id,
                        node_type="domain",
                        value_norm=normalized_candidate,
                        metadata={"discovered_by_identifier": pivot.value},
                    )
                    self.repository.add_edge(
                        run_id=run_id,
                        from_node_id=identifier_node_id,
                        to_node_id=child_domain_node_id,
                        relation_type="identifier_links_domain",
                        depth=work_item.depth + 1,
                        source="reverse_whois",
                        confidence=pivot.score,
                    )
                    self.repository.add_lineage(
                        run_id=run_id,
                        child_node_id=child_domain_node_id,
                        parent_node_id=domain_node_id,
                        depth=work_item.depth + 1,
                    )
                    if (collected is not None
                            and provider_data_policy(self.reverse_whois_provider).cross_run_cache):
                        collected.append({
                            "domain": normalized_candidate,
                            "source": "reverse_whois",
                            "confidence": pivot.score,
                            "id_type": pivot.id_type,
                            "id_value": pivot.value,
                            "relation_type": "related_domain_via_identifier",
                            "observation": reverse_observation.to_dict(),
                            "supporting_observations": (
                                [verification_observation.to_dict()]
                                if verification_observation is not None else []
                            ),
                        })

                    enqueue_key = (normalized_candidate, work_item.depth + 1)
                    if enqueue_key in enqueued:
                        continue
                    if self.repository.is_domain_processed(run_id, child_domain_node_id):
                        continue
                    cap_q = self.settings.max_pending_queue
                    if cap_q and len(queue) >= cap_q:
                        logger.warning(
                            "Pending queue ceiling (%d) reached; skipping enqueue of %s",
                            cap_q,
                            normalized_candidate,
                        )
                        continue
                    queue.push(
                        DomainWorkItem(
                            domain=normalized_candidate,
                            depth=work_item.depth + 1,
                            parent_domain_node_id=domain_node_id,
                        )
                    )
                    enqueued.add(enqueue_key)
        logger.info("timing reverse_whois_batch (all pivots) %.2fs", time.perf_counter() - t_rw_all)

    def _replay_cached(self, run_id, domain_node_id, work_item, children, queue, enqueued, depth_cap) -> None:
        """Rebuild a domain's known children from the cross-run cache instead of scraping."""
        child_depth = work_item.depth + 1
        for rec in children:
            dom = str(rec.get("domain", "")).strip()
            if not dom:
                continue
            try:
                validate_scan_target(dom)
            except SecurityError:
                continue
            conf = float(rec.get("confidence") or 0.0)
            id_value = str(rec.get("id_value") or "")
            id_type = str(rec.get("id_type") or "")
            source = str(rec.get("source") or "legacy_cache")
            relation_type = str(
                rec.get("relation_type")
                or ("related_domain_via_identifier" if id_value else "related_domain")
            )
            uses_identifier_edge = relation_type == "related_domain_via_identifier"

            serialized_observation = rec.get("observation")
            if isinstance(serialized_observation, dict):
                try:
                    cached_observation = Observation.from_dict(serialized_observation)
                except (KeyError, TypeError, ValueError):
                    cached_observation = None
            else:
                cached_observation = None
            if cached_observation is None:
                cached_observation = Observation.build(
                    subject_type="identifier" if uses_identifier_edge else "domain",
                    subject_value_norm=id_value if uses_identifier_edge else work_item.domain,
                    predicate="links_domain" if uses_identifier_edge else relation_type,
                    object_type="domain",
                    object_value_norm=dom,
                    source=source,
                    confidence=conf,
                    normalized={"replayed_from_legacy_cache": True, "identifier_type": id_type},
                    idempotency_key=(
                        f"legacy-cache:{work_item.domain}:{relation_type}:{id_type}:{id_value}:{dom}"
                    ),
                )
            observation_id = self.repository.add_observation(run_id, cached_observation)
            relationship_claim_id = self._add_relationship_claim(
                run_id=run_id,
                relation_type=relation_type,
                subject_domain=work_item.domain,
                object_domain=dom,
                score=conf,
                source=cached_observation.source,
                observation_id=observation_id,
            )
            for serialized_support in rec.get("supporting_observations") or []:
                if not isinstance(serialized_support, dict):
                    continue
                try:
                    support = Observation.from_dict(serialized_support)
                except (KeyError, TypeError, ValueError):
                    continue
                support_id = self.repository.add_observation(run_id, support)
                self.repository.link_claim_evidence(
                    relationship_claim_id,
                    support_id,
                    "supports",
                    min(max(float(support.confidence), 0.0), 1.0),
                    "cached independently verified supporting observation",
                )

            if uses_identifier_edge and id_value:
                pivot_evidence = self.repository.find_observations_for_object(
                    run_id,
                    subject_value_norm=work_item.domain,
                    object_value_norm=id_value,
                )
                if pivot_evidence:
                    pivot_observation_id = str(pivot_evidence[0]["id"])
                    pivot_source = str(pivot_evidence[0]["source"])
                else:
                    replayed_pivot = Observation.build(
                        subject_type="domain",
                        subject_value_norm=work_item.domain,
                        predicate="selected_pivot",
                        object_type="identifier",
                        object_value_norm=id_value,
                        source="relationship_engine",
                        confidence=conf,
                        normalized={"identifier_type": id_type, "replayed_from_cache": True},
                        idempotency_key=f"cache-pivot:{work_item.domain}:{id_type}:{id_value}",
                    )
                    pivot_observation_id = self.repository.add_observation(run_id, replayed_pivot)
                    pivot_source = replayed_pivot.source
                self._add_relationship_claim(
                    run_id=run_id,
                    relation_type="domain_has_identifier",
                    subject_domain=work_item.domain,
                    object_domain=id_value,
                    score=conf,
                    source=pivot_source,
                    observation_id=pivot_observation_id,
                    object_type="identifier",
                )

            child_node_id = self.repository.get_or_create_node(
                run_id=run_id, node_type="domain", value_norm=dom,
                metadata={"discovered_by": "cache"},
            )
            if uses_identifier_edge and id_value:
                identifier_node_id = self.repository.get_or_create_node(
                    run_id=run_id, node_type="identifier", value_norm=id_value,
                    metadata={"identifier_type": id_type},
                )
                self.repository.add_edge(run_id, domain_node_id, identifier_node_id,
                                         "domain_has_identifier", work_item.depth, "cache", conf)
                self.repository.add_edge(run_id, identifier_node_id, child_node_id,
                                         "identifier_links_domain", child_depth, "cache", conf)
            else:
                self.repository.add_edge(run_id, domain_node_id, child_node_id,
                                         relation_type, child_depth, "cache", conf)
            self.repository.add_lineage(run_id, child_node_id, domain_node_id, child_depth)

            if depth_cap is not None and child_depth > depth_cap:
                continue
            key = (dom, child_depth)
            if key in enqueued or self.repository.is_domain_processed(run_id, child_node_id):
                continue
            cap_q = self.settings.max_pending_queue
            if cap_q and len(queue) >= cap_q:
                continue
            queue.push(DomainWorkItem(domain=dom, depth=child_depth, parent_domain_node_id=domain_node_id))
            enqueued.add(key)

    # ── Main run loop ──────────────────────────────────────────────────

    def _print_status(
        self,
        step: int,
        depth: int,
        llm_calls: int,
        domains_found: int,
        current_domain: str,
        *,
        final: bool = False,
    ) -> None:
        """Print an inline status line that overwrites itself."""
        import sys
        line = (
            f"\r[Depth: {depth}] | Steps: {step} | LLM Calls: {llm_calls} "
            f"| Domains Found: {domains_found} | {'Done' if final else current_domain}..."
        )
        sys.stderr.write(f"{line:<120}")
        if final:
            sys.stderr.write("\n")
        sys.stderr.flush()

    async def run(
        self,
        root_domain: str,
        max_depth: int | None = None,
        pivot_top_k: int | None = None,
        resume: bool = False,
        force_refresh: bool = False,
    ) -> RunSummary:
        normalized_root = normalize_domain(root_domain)
        validate_scan_target(normalized_root)
        raw_depth = self.settings.default_max_depth if max_depth is None else max_depth
        depth_cap: int | None = None if raw_depth < 0 else raw_depth
        stored_max_depth = -1 if depth_cap is None else depth_cap
        resolved_pivot_top_k = pivot_top_k if pivot_top_k is not None else self.settings.pivot_top_k

        if depth_cap is None:
            logger.info("Run depth: unlimited (cap=%r from env/CLI)", raw_depth)
        else:
            logger.info("Run depth: capped at %d", depth_cap)

        # ── Resume or fresh run ─────────────────────────────────────────
        resumed = False
        legacy_pending: list[tuple[str, int]] = []
        if resume:
            prev = self.repository.get_latest_resumable_run(normalized_root)
            if prev:
                run_id = str(prev["id"])
                self.repository.requeue_in_progress_tasks(run_id)
                durable_pending = self.repository.count_runnable_tasks(run_id)
                if durable_pending == 0:
                    legacy_pending = self.repository.get_resumable_queue(run_id)
                pending_count = durable_pending + len(legacy_pending)
                skipped = self.repository.count_processed_domains(run_id)
                if pending_count:
                    logger.info(
                        "↩ Resuming run %s (skipping %d already-processed domains, %d pending)",
                        run_id, skipped, pending_count,
                    )
                    self.repository.conn.execute(
                        "UPDATE runs SET status = 'running', completed_at = NULL WHERE id = ?",
                        (run_id,),
                    )
                    self.repository.conn.commit()
                    resumed = True

        if not resumed:
            run_id = self.repository.create_run(
                root_domain=normalized_root,
                max_depth=stored_max_depth,
                pivot_top_k=resolved_pivot_top_k,
                provider_profile=self.settings.provider_tier,
                max_provider_calls=self.settings.max_provider_calls,
                max_billable_units=self.settings.max_billable_units,
                run_mode=self.settings.run_mode,
                llm_model=self.settings.llm_model or "ollama/qwen2.5:7b-instruct",
                fast_model=self.settings.fast_model,
                model_routing_policy=(
                    "economical-first-v1" if self.settings.fast_model else "single-model-v1"
                ),
                llm_policy_version=(
                    "deterministic-escalation-v1"
                    if self.settings.llm_escalate_only else "always-model-v1"
                ),
                cache_mode="refresh" if force_refresh else "reuse",
                cloud_approved=self.settings.cloud_approved,
                max_model_calls=self.settings.max_model_calls,
                max_model_input_tokens=self.settings.max_model_input_tokens,
                max_model_output_tokens=self.settings.max_model_output_tokens,
                max_cloud_tokens=self.settings.max_cloud_tokens,
                max_cloud_cost_microusd=math.ceil(
                    self.settings.max_cloud_cost_usd * 1_000_000
                ),
                model_price_catalog_version=PRICE_CATALOG_VERSION,
            )

        queue = DomainQueue(
            self.repository,
            run_id,
            lease_seconds=max(30, self.settings.per_domain_timeout_sec + 30),
        )
        enqueued: set[tuple[str, int]] = set()

        if resumed:
            root_domain_node_id = self.repository.get_or_create_node(
                run_id=run_id,
                node_type="domain",
                value_norm=normalized_root,
            )
            for dom, d in legacy_pending:
                queue.push(DomainWorkItem(domain=dom, depth=d, parent_domain_node_id=None))
                enqueued.add((dom, d))
        else:
            root_domain_node_id = self.repository.get_or_create_node(
                run_id=run_id,
                node_type="domain",
                value_norm=normalized_root,
                metadata={"is_root": True},
            )
            queue.push(DomainWorkItem(domain=normalized_root, depth=0, parent_domain_node_id=None))
            enqueued.add((normalized_root, 0))

        step_counter = 0
        llm_calls = 0

        logger.info("Starting BFS orchestration run_id=%s for %s", run_id, normalized_root)

        while queue:
            g_max = self.settings.global_max_nodes
            if g_max and self.repository.count_nodes(run_id) >= g_max:
                logger.warning("Global node limit reached (%d); halting crawl", g_max)
                break

            work_item = queue.pop()

            domain_node_id = self.repository.get_or_create_node(
                run_id=run_id,
                node_type="domain",
                value_norm=work_item.domain,
            )

            if self.repository.is_domain_processed(run_id, domain_node_id):
                if work_item.task_id:
                    self.repository.complete_run_task(work_item.task_id)
                continue

            step_counter += 1
            cur_found = self.repository.count_domain_nodes(run_id)
            self._print_status(step_counter, work_item.depth, llm_calls, cur_found, work_item.domain)

            if work_item.parent_domain_node_id:
                self.repository.add_lineage(
                    run_id=run_id,
                    child_node_id=domain_node_id,
                    parent_node_id=work_item.parent_domain_node_id,
                    depth=work_item.depth,
                )

            # ── Cross-run scrape cache lookup ───────────────────────────
            use_cache = not force_refresh and self.cross_run_cache_allowed
            ttl_h = getattr(self.settings, "cache_ttl_hours", 168)
            cached_entry = self.repository.get_domain_cache(work_item.domain) if use_cache else None

            if cached_entry and _cache_is_fresh(str(cached_entry["last_scraped"]), ttl_h):
                logger.info(
                    "Cache hit for %s (scraped %s, TTL %dh) — skipping network/LLM",
                    work_item.domain, cached_entry["last_scraped"], ttl_h,
                )
                self._replay_cached_observations(
                    run_id,
                    domain_node_id,
                    work_item.depth,
                    cached_entry.get("observations", []),
                )
                self._replay_cached(
                    run_id, domain_node_id, work_item,
                    cached_entry.get("children", []), queue, enqueued, depth_cap,
                )
                self.repository.mark_domain_processed(
                    run_id=run_id,
                    domain_node_id=domain_node_id,
                    depth=work_item.depth,
                )
                if work_item.task_id:
                    self.repository.complete_run_task(work_item.task_id)
                continue

            # ── Concurrent data gathering ──────────────────────────────
            t_start = time.perf_counter()
            is_apex = work_item.domain == registrable_domain(work_item.domain)
            do_sub_enum = (
                self.settings.map_subdomains
                and is_apex
                and (depth_cap is None or work_item.depth < depth_cap)
            )

            (whois_provider_result, registration_evidence_results, basic_provider_result,
             dns_provider_result, subdomain_provider_result, history_provider_result) = await self._gather_all_data(
                work_item.domain, do_subdomain_enum=do_sub_enum, run_id=run_id
            )
            from reconrelate.core.types import BasicIntelRecord, WhoisRecord
            whois_record = whois_provider_result.data or WhoisRecord(domain=work_item.domain)
            basic_intel = basic_provider_result.data or BasicIntelRecord(domain=work_item.domain)
            dns_result = dns_provider_result.data
            subdomains = subdomain_values(subdomain_provider_result.data)
            t_gather = time.perf_counter() - t_start

            # Persist immutable source observations before updating graph projections.
            observation_index: dict[tuple[str, str], list[tuple[str, Observation]]] = {}
            persisted_observations: list[tuple[str, Observation]] = []
            with self.repository.batch():
                for provider_result in (
                    *registration_evidence_results,
                    basic_provider_result,
                    dns_provider_result,
                    subdomain_provider_result,
                    history_provider_result,
                ):
                    for observation in observations_from_result(provider_result):
                        observation_id = self.repository.add_observation(run_id, observation)
                        observation_index.setdefault(
                            (observation.predicate, observation.object_value_norm or ""), []
                        ).append((observation_id, observation))
                        persisted_observations.append((observation_id, observation))

                if whois_record.registrant_org:
                    self.repository.set_node_metadata(domain_node_id, {"whois_org": whois_record.registrant_org})

                for observation_id, observation in persisted_observations:
                    self._add_infrastructure_from_observation(
                        run_id=run_id,
                        domain_node_id=domain_node_id,
                        depth=work_item.depth,
                        observation_id=observation_id,
                        observation=observation,
                    )

            cache_collected: list[dict] = []

            # Process subdomains as child domain nodes
            if subdomains:
                sub_count = 0
                with self.repository.batch():
                    for sub in subdomains:
                        try:
                            normalized_sub = normalize_domain(sub)
                            validate_scan_target(normalized_sub)
                        except SecurityError:
                            continue
                        except Exception:
                            continue

                        if normalized_sub == work_item.domain:
                            continue

                        evidence = observation_index.get(("has_subdomain", normalized_sub), [])
                        if not evidence:
                            continue
                        observation_id, subdomain_observation = evidence[0]
                        claim_id = self._add_relationship_claim(
                            run_id=run_id,
                            relation_type="domain_has_subdomain",
                            subject_domain=work_item.domain,
                            object_domain=normalized_sub,
                            score=0.9,
                            source=subdomain_observation.source,
                            observation_id=observation_id,
                        )
                        for extra_id, extra_observation in evidence[1:]:
                            self.repository.link_claim_evidence(
                                claim_id,
                                extra_id,
                                "supports",
                                extra_observation.confidence,
                                f"additional {extra_observation.source} subdomain observation",
                            )

                        sub_node_id = self.repository.get_or_create_node(
                            run_id=run_id,
                            node_type="domain",
                            value_norm=normalized_sub,
                            metadata={"discovered_by": subdomain_provider_result.provider},
                        )
                        self.repository.add_edge(
                            run_id=run_id,
                            from_node_id=domain_node_id,
                            to_node_id=sub_node_id,
                            relation_type="domain_has_subdomain",
                            depth=work_item.depth + 1,
                            source=subdomain_provider_result.provider,
                        )
                        self.repository.add_lineage(
                            run_id=run_id,
                            child_node_id=sub_node_id,
                            parent_node_id=domain_node_id,
                            depth=work_item.depth + 1,
                        )
                        sub_count += 1
                        cache_collected.append({
                            "domain": normalized_sub,
                            "source": "subdomain_enum",
                            "confidence": 1.0,
                            "id_type": "",
                            "id_value": "",
                            "relation_type": "domain_has_subdomain",
                            "observation": subdomain_observation.to_dict(),
                        })

                        enqueue_key = (normalized_sub, work_item.depth + 1)
                        if depth_cap is not None and work_item.depth + 1 > depth_cap:
                            continue
                        if enqueue_key in enqueued:
                            continue
                        if self.repository.is_domain_processed(run_id, sub_node_id):
                            continue

                        cap_q = self.settings.max_pending_queue
                        if cap_q and len(queue) >= cap_q:
                            logger.warning("Pending queue ceiling (%d) reached; skipping subdomain %s", cap_q, normalized_sub)
                            continue

                        queue.push(DomainWorkItem(domain=normalized_sub, depth=work_item.depth + 1, parent_domain_node_id=domain_node_id))
                        enqueued.add(enqueue_key)

                logger.info("Discovered %d subdomains for %s", sub_count, work_item.domain)

            # ── Relationship Engine Pivot Selection ─────────────────────
            t_llm_start = time.perf_counter()
            pivots = await self.relationship_engine.select_pivots(
                domain=work_item.domain,
                whois=whois_record,
                basic_intel=basic_intel,
                top_k=resolved_pivot_top_k,
                subdomains=subdomains,
                run_metadata={"run_id": run_id, "depth": work_item.depth},
            )
            t_llm = time.perf_counter() - t_llm_start
            llm_calls = int(getattr(self.relationship_engine, "sdk_calls", llm_calls))
            # Repaint the status line now that this domain's model call has actually happened.
            # The line printed before the call necessarily still showed the previous domain's
            # count, so on a single-domain run it read "LLM Calls: 0" even when a call succeeded.
            self._print_status(step_counter, work_item.depth, llm_calls, cur_found, work_item.domain)

            logger.info(
                "timing %s: gather=%.2fs llm=%.2fs total=%.2fs (%d subdomains, %d pivots)",
                work_item.domain, t_gather, t_llm, time.perf_counter() - t_start,
                len(subdomains), len(pivots)
            )

            # ── Concurrent Reverse-WHOIS Pivoting (email/tracker/ns/phone only) ──
            await self._reverse_whois_batch(
                pivots=pivots,
                run_id=run_id,
                domain_node_id=domain_node_id,
                work_item=work_item,
                queue=queue,
                enqueued=enqueued,
                depth_cap=depth_cap,
                collected=cache_collected,
            )

            # ── Acquisitions: resolve org→domain via Wikidata P856 (reliable, no search) ──
            await self._expand_acquisitions(
                pivots=pivots,
                run_id=run_id,
                domain_node_id=domain_node_id,
                work_item=work_item,
                queue=queue,
                enqueued=enqueued,
                depth_cap=depth_cap,
                collected=cache_collected,
            )

            # Store mapping in cross-run cache
            if self.cross_run_cache_allowed:
                self.repository.upsert_domain_cache(
                    work_item.domain,
                    cache_collected,
                    [
                        observation.to_dict() for _, observation in persisted_observations
                        if observation.cache_allowed
                    ],
                )
            self.repository.mark_domain_processed(
                run_id=run_id,
                domain_node_id=domain_node_id,
                depth=work_item.depth,
            )
            if work_item.task_id:
                self.repository.complete_run_task(work_item.task_id)

        final_found = self.repository.count_domain_nodes(run_id)
        self._print_status(step_counter, work_item.depth if 'work_item' in locals() else 0, llm_calls, final_found, "", final=True)

        self.repository.mark_run_completed(run_id)
        summary = self.repository.get_run_summary(run_id)
        logger.info(
            "Finished run %s: domains=%d identifiers=%d edges=%d",
            summary.run_id, summary.domains_count, summary.identifiers_count, summary.edges_count
        )
        return summary
