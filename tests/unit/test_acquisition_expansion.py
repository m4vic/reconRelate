import asyncio
from contextlib import contextmanager

from reconrelate.config.settings import Settings
from reconrelate.core.types import PivotCandidate
from reconrelate.orchestrator.orchestrator import DomainQueue, DomainWorkItem, RunOrchestrator


class FakeAcq:
    def __init__(self, raising: bool = False) -> None:
        self.raising = raising

    async def related_orgs(self, name, max_results=5):  # noqa: ANN001
        if self.raising:
            raise RuntimeError("network down")
        return [
            {"relation": "subsidiary", "org": "Fitbit", "qid": "Q1", "domain": "fitbit.com"},
            {"relation": "parent", "org": "Alphabet Inc.", "qid": "Q2", "domain": "abc.xyz"},
        ]


class FakeHierarchy:
    async def related_orgs(self, name, max_results=5):  # noqa: ANN001
        return [{
            "relation": "direct_accounting_parent",
            "org": "Alphabet Inc.",
            "lei": "LEI-PARENT",
            "subject_lei": "LEI-CHILD",
            "source_record_id": "LEI-CHILD:direct:LEI-PARENT",
            "domain": "",
        }]


class FakeSecRelation:
    async def related_orgs(self, name, max_results=5):  # noqa: ANN001
        return [{
            "relation": "acquired", "org": "Target Labs LLC", "domain": "",
            "cik": "0000123456", "source_record_id": "0000123456-26-000001",
            "filing_date": "2026-01-02", "filing_url": "https://www.sec.gov/filing",
            "supporting_text": "The Company completed the acquisition of Target Labs LLC.",
        }]


class FakeRepository:
    def __init__(self) -> None:
        self.nodes: dict[tuple[str, str], str] = {}
        self.edges: list[dict] = []
        self.lineage: list[tuple] = []
        self.observations: list[object] = []
        self.claims: list[object] = []
        self.claim_evidence: list[tuple] = []

    @contextmanager
    def batch(self):
        yield

    def get_or_create_node(self, run_id, node_type, value_norm, metadata):  # noqa: ANN001
        key = (node_type, value_norm)
        self.nodes.setdefault(key, f"node-{len(self.nodes) + 1}")
        return self.nodes[key]

    def add_edge(self, run_id, from_node_id, to_node_id, relation_type, depth, source, confidence):  # noqa: ANN001
        self.edges.append({
            "run_id": run_id,
            "from": from_node_id,
            "to": to_node_id,
            "relation": relation_type,
            "depth": depth,
            "source": source,
            "confidence": confidence,
        })

    def add_lineage(self, run_id, child_node_id, parent_node_id, depth):  # noqa: ANN001
        self.lineage.append((run_id, child_node_id, parent_node_id, depth))

    def add_observation(self, run_id, observation):  # noqa: ANN001
        self.observations.append(observation)
        return f"observation-{len(self.observations)}"

    def add_claim(self, run_id, claim):  # noqa: ANN001
        self.claims.append(claim)
        return f"claim-{len(self.claims)}"

    def link_claim_evidence(self, claim_id, observation_id, polarity, weight, reason):  # noqa: ANN001
        self.claim_evidence.append((claim_id, observation_id, polarity, weight, reason))

    def is_domain_processed(self, run_id, domain_node_id):  # noqa: ANN001
        return False


def _orch(acq, expand: bool) -> RunOrchestrator:
    settings = Settings.from_env()
    settings.expand_acquisitions = expand
    return RunOrchestrator(
        repository=FakeRepository(),
        whois_provider=None,
        basic_info_provider=None,
        reverse_whois_provider=None,
        crtsh_provider=None,
        hackertarget_provider=None,
        dns_provider=None,
        relationship_engine=None,
        settings=settings,
        acquisitions_provider=acq,
    )


def _pivots():
    return [PivotCandidate("org", "google", 0.75, "whois")]


async def _expand(orch: RunOrchestrator):
    queue = DomainQueue()
    collected: list[dict] = []
    await orch._expand_acquisitions(
        pivots=_pivots(),
        run_id="run-1",
        domain_node_id="root-node",
        work_item=DomainWorkItem("google.com", 0, None),
        queue=queue,
        enqueued=set(),
        depth_cap=2,
        collected=collected,
    )
    return queue, collected


def test_expands_org_pivots_when_enabled() -> None:
    orch = _orch(FakeAcq(), expand=True)
    queue, collected = asyncio.run(_expand(orch))

    assert {item["domain"] for item in collected} == {"fitbit.com", "abc.xyz"}
    assert len(queue) == 2
    assert {edge["relation"] for edge in orch.repository.edges} == {
        "acquisition_subsidiary",
        "acquisition_parent",
    }
    assert len(orch.repository.claims) == 2
    assert len(orch.repository.claim_evidence) == 2
    assert {claim.claim_type for claim in orch.repository.claims} == {
        "acquisition_subsidiary",
        "acquisition_parent",
    }


def test_no_expansion_when_disabled() -> None:
    queue, collected = asyncio.run(_expand(_orch(FakeAcq(), expand=False)))
    assert len(queue) == 0
    assert collected == []


def test_fail_safe_on_provider_error() -> None:
    queue, collected = asyncio.run(_expand(_orch(FakeAcq(raising=True), expand=True)))
    assert len(queue) == 0
    assert collected == []


def test_each_org_expanded_once() -> None:
    orch = _orch(FakeAcq(), expand=True)
    first_queue, first_collected = asyncio.run(_expand(orch))
    second_queue, second_collected = asyncio.run(_expand(orch))

    assert len(first_queue) == 2
    assert len(first_collected) == 2
    assert len(second_queue) == 0
    assert second_collected == []


def test_multiple_sources_run_and_domainless_hierarchy_is_preserved_as_evidence() -> None:
    wikidata = FakeAcq()
    gleif = FakeHierarchy()
    setattr(wikidata, "__reconrelate_provider__", "wikidata")
    setattr(gleif, "__reconrelate_provider__", "gleif")
    orch = _orch([wikidata, gleif], expand=True)

    queue, collected = asyncio.run(_expand(orch))

    assert len(queue) == 2
    assert len(collected) == 2
    hierarchy = [
        item for item in orch.repository.observations
        if item.source == "gleif" and item.object_type == "organization"
    ]
    assert len(hierarchy) == 1
    assert hierarchy[0].predicate == "direct_accounting_parent"
    assert hierarchy[0].object_value_norm == "Alphabet Inc."
    assert hierarchy[0].source_record_id == "LEI-CHILD:direct:LEI-PARENT"
    assert not any(edge["source"] == "gleif" for edge in orch.repository.edges)


def test_one_corporate_source_failure_does_not_suppress_another() -> None:
    failed = FakeAcq(raising=True)
    working = FakeAcq()
    setattr(failed, "__reconrelate_provider__", "failed")
    setattr(working, "__reconrelate_provider__", "wikidata")
    queue, collected = asyncio.run(_expand(_orch([failed, working], expand=True)))
    assert len(queue) == 2
    assert len(collected) == 2


def test_sec_filing_provenance_is_retained_without_creating_domain_edge() -> None:
    sec = FakeSecRelation()
    setattr(sec, "__reconrelate_provider__", "sec-edgar")
    orch = _orch(sec, expand=True)
    queue, collected = asyncio.run(_expand(orch))
    observation = orch.repository.observations[0]
    assert observation.source == "sec-edgar"
    assert observation.source_record_id == "0000123456-26-000001"
    assert observation.normalized["cik"] == "0000123456"
    assert observation.normalized["filing_url"] == "https://www.sec.gov/filing"
    assert "completed the acquisition" in observation.normalized["supporting_text"]
    assert len(queue) == 0
    assert collected == []
    assert orch.repository.edges == []


def test_cached_acquisition_replays_as_direct_acquisition_edges() -> None:
    fresh = _orch(FakeAcq(), expand=True)
    _, collected = asyncio.run(_expand(fresh))
    replay = _orch(FakeAcq(), expand=True)
    queue = DomainQueue()
    replay._replay_cached(
        "run-2", "root-node", DomainWorkItem("google.com", 0, None),
        collected, queue, set(), 2,
    )
    assert {edge["relation"] for edge in replay.repository.edges} == {
        "acquisition_subsidiary", "acquisition_parent",
    }
    assert all(edge["relation"] != "domain_has_identifier" for edge in replay.repository.edges)
    assert len(replay.repository.claims) == 2
