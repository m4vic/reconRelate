import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from reconrelate.config.settings import Settings
from reconrelate.core.types import BasicIntelRecord, PivotCandidate, WhoisRecord
from reconrelate.db.db import get_connection, init_db
from reconrelate.db.repositories import GraphRepository
from reconrelate.orchestrator.orchestrator import RunOrchestrator, _cache_is_fresh


# ── freshness helper ────────────────────────────────────────────────────

def test_fresh_entry_is_fresh() -> None:
    assert _cache_is_fresh(datetime.now(timezone.utc).isoformat(), 168)


def test_stale_entry_is_not_fresh() -> None:
    old = (datetime.now(timezone.utc) - timedelta(hours=200)).isoformat()
    assert not _cache_is_fresh(old, 168)


def test_ttl_zero_disables_cache() -> None:
    assert not _cache_is_fresh(datetime.now(timezone.utc).isoformat(), 0)


def test_bad_timestamp_is_not_fresh() -> None:
    assert not _cache_is_fresh("not-a-date", 168)


# ── repository roundtrip ────────────────────────────────────────────────

def _repo() -> GraphRepository:
    conn = get_connection(":memory:")
    init_db(conn)
    return GraphRepository(conn)


def test_cache_roundtrip() -> None:
    repo = _repo()
    children = [{"domain": "b.com", "source": "reverse_whois", "confidence": 0.9,
                 "id_type": "email", "id_value": "a@x.com"}]
    observations = [{"predicate": "resolves_to", "object_value_norm": "192.0.2.1"}]
    repo.upsert_domain_cache("a.com", children, observations)
    got = repo.get_domain_cache("a.com")
    assert got is not None
    assert got["children"] == children
    assert got["observations"] == observations
    assert got["last_scraped"]
    assert repo.get_domain_cache("missing.com") is None


def test_cache_upsert_replaces() -> None:
    repo = _repo()
    repo.upsert_domain_cache("a.com", [{"domain": "old.com"}])
    repo.upsert_domain_cache("a.com", [{"domain": "new.com"}])
    assert repo.get_domain_cache("a.com")["children"] == [{"domain": "new.com"}]


# ── integration: second run replays from cache, scrapes nothing ─────────

class _Named:
    """A fake provider that counts calls. Serves whois/basic/dns (lookup) and
    subdomain/reverse-whois (search) roles depending on `kind`."""

    def __init__(self, calls, kind):
        self._calls = calls
        self._kind = kind

    async def lookup(self, domain):
        self._calls[self._kind] += 1
        if self._kind == "whois":
            return WhoisRecord(domain=domain)
        if self._kind == "basic":
            return BasicIntelRecord(domain=domain)
        return None  # dns

    async def search(self, *args, **kwargs):
        self._calls[self._kind] += 1
        ident = kwargs.get("identifier") or (args[0] if args else None)
        if self._kind == "reverse_whois" and getattr(ident, "value", "") == "a@x.com":
            return ["child1.com"]  # the seed's identifier finds one child
        return []


class _FakeRelEngine:
    def __init__(self, calls):
        self._calls = calls

    async def select_pivots(self, domain, whois, basic_intel, top_k, subdomains, run_metadata):
        self._calls["select_pivots"] += 1
        if domain == "roshi.com":
            return [PivotCandidate(id_type="email", value="a@x.com", score=0.9, reason="seed")]
        return []


def _make_orchestrator(repo, calls) -> RunOrchestrator:
    settings = Settings.from_env()
    settings.per_domain_timeout_sec = 3600
    settings.global_max_nodes = 1000
    settings.cache_ttl_hours = 168
    return RunOrchestrator(
        repository=repo,
        whois_provider=_Named(calls, "whois"),
        basic_info_provider=_Named(calls, "basic"),
        reverse_whois_provider=_Named(calls, "reverse_whois"),
        crtsh_provider=_Named(calls, "subdomains"),
        hackertarget_provider=_Named(calls, "subdomains"),
        dns_provider=_Named(calls, "dns"),
        relationship_engine=_FakeRelEngine(calls),
        settings=settings,
    )


def test_second_run_replays_cache_without_scraping() -> None:
    repo = _repo()
    calls: dict[str, int] = defaultdict(int)
    orch = _make_orchestrator(repo, calls)

    s1 = asyncio.run(orch.run("roshi.com", max_depth=2))
    assert s1.domains_count == 2          # roshi.com + child1.com
    assert s1.identifiers_count == 1      # a@x.com
    assert sum(calls.values()) > 0        # run 1 actually scraped
    first_claims = repo.get_claims_with_evidence(s1.run_id)
    first_projection = repo.get_run_graph(s1.run_id)["claim_projection"]
    assert {claim["claim_type"] for claim in first_claims} == {
        "domain_has_identifier", "related_domain_via_identifier",
    }
    related_first = next(
        claim for claim in first_claims if claim["claim_type"] == "related_domain_via_identifier"
    )
    assert len(related_first["evidence"]) == 1

    calls.clear()
    s2 = asyncio.run(orch.run("roshi.com", max_depth=2))
    assert sum(calls.values()) == 0       # run 2 hit the cache — zero scraping
    assert s2.domains_count == 2          # same tree rebuilt from cache
    assert s2.identifiers_count == 1
    second_claims = repo.get_claims_with_evidence(s2.run_id)
    assert {claim["claim_type"] for claim in second_claims} == {
        "domain_has_identifier", "related_domain_via_identifier",
    }
    related_second = next(
        claim for claim in second_claims if claim["claim_type"] == "related_domain_via_identifier"
    )
    assert related_second["score"] == related_first["score"]
    assert related_second["evidence"][0]["source"] == related_first["evidence"][0]["source"]
    assert related_second["evidence"][0]["predicate"] == "links_domain"
    assert repo.get_run_graph(s2.run_id)["claim_projection"] == first_projection


def test_refresh_flag_bypasses_cache() -> None:
    repo = _repo()
    calls: dict[str, int] = defaultdict(int)
    orch = _make_orchestrator(repo, calls)

    asyncio.run(orch.run("roshi.com", max_depth=2))
    calls.clear()
    asyncio.run(orch.run("roshi.com", max_depth=2, force_refresh=True))
    assert sum(calls.values()) > 0        # --refresh re-scraped despite the warm cache
