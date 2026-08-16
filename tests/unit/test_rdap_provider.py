import asyncio

import pytest

from reconrelate.core.errors import ProviderMalformedError
from reconrelate.data_gathering.rdap_provider import RdapProvider
from reconrelate.config.settings import Settings
from reconrelate.core.types import WhoisRecord
from reconrelate.db.db import get_connection, init_db
from reconrelate.db.repositories import GraphRepository
from reconrelate.orchestrator.orchestrator import RunOrchestrator


class FakeRdap(RdapProvider):
    def __init__(self, responses):
        super().__init__()
        self.responses = list(responses)
        self.urls: list[str] = []

    async def _get_json(self, url: str, *, max_bytes: int):
        self.urls.append(url)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _bootstrap(*entries):
    return {"version": "1.0", "services": list(entries)}


def test_longest_bootstrap_match_and_thick_domain_normalization() -> None:
    provider = FakeRdap([
        _bootstrap(
            [["com"], ["https://registry.example/rdap/"]],
            [["example.com"], ["https://specific.example/service/"]],
        ),
        {
            "objectClassName": "domain",
            "handle": "EXAMPLE-1",
            "status": ["active"],
            "nameservers": [{"ldhName": "NS2.EXAMPLE.NET."}, {"ldhName": "ns1.example.net"}],
            "events": [
                {"eventAction": "registration", "eventDate": "2000-01-01T00:00:00Z"},
                {"eventAction": "expiration", "eventDate": "2030-01-01T00:00:00Z"},
            ],
            "entities": [{
                "roles": ["registrant"],
                "vcardArray": ["vcard", [
                    ["fn", {}, "text", "Jane Registrant"],
                    ["org", {}, "text", ["Acme", "Labs"]],
                    ["email", {}, "text", "security@acme.example"],
                    ["tel", {}, "text", "+1-555-0100"],
                ]],
            }],
        },
    ])
    record = asyncio.run(provider.lookup("shop.example.com"))
    assert provider.urls[1] == "https://specific.example/service/domain/shop.example.com"
    assert record.registrant_org == "Acme Labs"
    assert record.registrant_email == "security@acme.example"
    assert record.nameservers == ["ns1.example.net", "ns2.example.net"]
    assert record.creation_date == "2000-01-01T00:00:00Z"
    assert record.raw["source"] == "rdap-iana"
    assert "entities" not in record.raw


def test_redacted_thin_registry_follows_one_related_domain_link_and_merges() -> None:
    provider = FakeRdap([
        _bootstrap([["com"], ["https://registry.example/rdap/"]]),
        {
            "objectClassName": "domain",
            "handle": "THIN-1",
            "nameservers": [{"ldhName": "ns1.example.net"}],
            "events": [{"eventAction": "registration", "eventDate": "2001-01-01Z"}],
            "entities": [{
                "roles": ["registrant"],
                "vcardArray": ["vcard", [["org", {}, "text", "REDACTED FOR PRIVACY"]]],
            }],
            "links": [{
                "rel": "related", "type": "application/rdap+json",
                "href": "https://registrar.example/rdap/domain/example.com",
            }],
        },
        {
            "objectClassName": "domain",
            "entities": [{
                "roles": ["registrant"],
                "vcardArray": ["vcard", [["org", {}, "text", "Acme Corporation"]]],
            }],
        },
    ])
    record = asyncio.run(provider.lookup("example.com"))
    assert record.registrant_org == "Acme Corporation"
    assert record.creation_date == "2001-01-01Z"
    assert record.nameservers == ["ns1.example.net"]
    assert record.raw["related_endpoint_host"] == "registrar.example"
    assert len(provider.urls) == 3


def test_non_https_bootstrap_service_fails_closed() -> None:
    provider = FakeRdap([_bootstrap([["com"], ["http://unsafe.example/rdap/"]])])
    with pytest.raises(ProviderMalformedError, match="no HTTPS services"):
        asyncio.run(provider.lookup("example.com"))


def test_domain_not_found_is_explicit_empty_record() -> None:
    provider = FakeRdap([
        _bootstrap([["com"], ["https://registry.example/rdap/"]]),
        None,
    ])
    record = asyncio.run(provider.lookup("example.com"))
    assert record.raw == {"source": "rdap-iana", "not_found": True}
    assert not record.registrant_org


def test_alternate_https_service_is_tried_after_primary_failure() -> None:
    provider = FakeRdap([
        _bootstrap([["com"], [
            "https://primary.example/rdap/", "https://secondary.example/rdap/",
        ]]),
        RuntimeError("primary unavailable"),
        {"objectClassName": "domain", "events": []},
    ])
    record = asyncio.run(provider.lookup("example.com"))
    assert record.raw["endpoint_host"] == "secondary.example"
    assert provider.urls[-1].startswith("https://secondary.example/")


def test_bootstrap_is_cached_between_lookups() -> None:
    domain = {"objectClassName": "domain", "entities": []}
    provider = FakeRdap([
        _bootstrap([["com"], ["https://registry.example/rdap/"]]),
        domain,
        domain,
    ])

    async def run_twice() -> None:
        await provider.lookup("one.com")
        await provider.lookup("two.com")

    asyncio.run(run_twice())
    assert provider.urls.count("https://data.iana.org/rdap/dns.json") == 1


def test_non_domain_object_is_rejected() -> None:
    provider = FakeRdap([
        _bootstrap([["com"], ["https://registry.example/rdap/"]]),
        {"objectClassName": "entity"},
    ])
    with pytest.raises(ProviderMalformedError, match="not a domain"):
        asyncio.run(provider.lookup("example.com"))


class _Registration:
    def __init__(self, name: str, record: WhoisRecord) -> None:
        self.__reconrelate_provider__ = name
        self.record = record
        self.calls = 0

    async def lookup(self, domain: str) -> WhoisRecord:
        self.calls += 1
        return self.record


def _orchestrator(providers: list[_Registration]):
    conn = get_connection(":memory:")
    init_db(conn)
    repo = GraphRepository(conn)
    settings = Settings.from_env()
    orchestrator = RunOrchestrator(
        repository=repo,
        whois_provider=providers,
        basic_info_provider=None,
        reverse_whois_provider=None,
        crtsh_provider=None,
        hackertarget_provider=None,
        dns_provider=None,
        relationship_engine=object(),
        settings=settings,
    )
    return repo, orchestrator


def test_registration_cascade_preserves_sources_and_fills_missing_identity() -> None:
    rdap = _Registration("rdap-iana", WhoisRecord(
        domain="example.com", nameservers=["ns.rdap.example"],
        creation_date="2000-01-01Z", raw={"source": "rdap-iana"},
    ))
    legacy = _Registration("python-whois", WhoisRecord(
        domain="example.com", registrant_org="Acme Inc",
        creation_date="legacy-date", raw={"source": "python-whois"},
    ))
    repo, orchestrator = _orchestrator([rdap, legacy])
    run_id = repo.create_run("example.com", 0, 1)
    merged, evidence, *_ = asyncio.run(orchestrator._gather_all_data(
        "example.com", do_subdomain_enum=False, run_id=run_id
    ))
    assert [item.provider for item in evidence] == ["rdap-iana", "python-whois"]
    assert merged.data.registrant_org == "Acme Inc"
    assert merged.data.creation_date == "2000-01-01Z"
    assert merged.data.nameservers == ["ns.rdap.example"]
    assert (rdap.calls, legacy.calls) == (1, 1)


def test_registration_cascade_stops_after_rdap_identity() -> None:
    rdap = _Registration("rdap-iana", WhoisRecord(
        domain="example.com", registrant_org="Authoritative Org", raw={"source": "rdap-iana"},
    ))
    legacy = _Registration("python-whois", WhoisRecord(
        domain="example.com", registrant_org="Should Not Run", raw={"source": "python-whois"},
    ))
    repo, orchestrator = _orchestrator([rdap, legacy])
    run_id = repo.create_run("example.com", 0, 1)
    merged, evidence, *_ = asyncio.run(orchestrator._gather_all_data(
        "example.com", do_subdomain_enum=False, run_id=run_id
    ))
    assert merged.data.registrant_org == "Authoritative Org"
    assert [item.provider for item in evidence] == ["rdap-iana"]
    assert legacy.calls == 0


def test_full_run_persists_rdap_and_legacy_evidence_separately() -> None:
    class NoPivots:
        async def select_pivots(self, **kwargs):
            return []

    rdap = _Registration("rdap-iana", WhoisRecord(
        domain="example.com", creation_date="2000-01-01Z",
        nameservers=["ns.rdap.example"], raw={"source": "rdap-iana"},
    ))
    legacy = _Registration("python-whois", WhoisRecord(
        domain="example.com", registrant_org="Acme Inc", raw={"source": "python-whois"},
    ))
    repo, orchestrator = _orchestrator([rdap, legacy])
    orchestrator.relationship_engine = NoPivots()
    orchestrator.settings.cache_ttl_hours = 0
    summary = asyncio.run(orchestrator.run("example.com", max_depth=0))
    graph = repo.get_run_graph(summary.run_id)
    registration = [
        item for item in graph["observations"]
        if item["source"] in {"rdap-iana", "python-whois"}
    ]
    assert {(item["source"], item["predicate"]) for item in registration} >= {
        ("rdap-iana", "created_at"),
        ("rdap-iana", "has_nameserver"),
        ("python-whois", "registered_by_org"),
    }
