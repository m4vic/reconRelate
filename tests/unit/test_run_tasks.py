import asyncio
from pathlib import Path

from reconrelate.config.settings import Settings
from reconrelate.core.types import BasicIntelRecord, WhoisRecord
from reconrelate.data_gathering.dns_provider import DNSResult
from reconrelate.db.db import get_connection, init_db
from reconrelate.db.repositories import GraphRepository
from reconrelate.orchestrator.orchestrator import RunOrchestrator


def _repo() -> GraphRepository:
    conn = get_connection(":memory:")
    init_db(conn)
    return GraphRepository(conn)


def test_enqueue_is_idempotent_and_claim_is_leased() -> None:
    repo = _repo()
    run_id = repo.create_run("example.com", 1, 3)
    first = repo.enqueue_run_task(
        run_id, task_type="map_domain", idempotency_key="domain:example.com",
        payload={"domain": "example.com", "depth": 0, "parent_domain_node_id": None},
    )
    second = repo.enqueue_run_task(
        run_id, task_type="map_domain", idempotency_key="domain:example.com",
        payload={"domain": "changed.example", "depth": 9},
    )
    assert first == second
    task = repo.claim_run_task(run_id, lease_seconds=60)
    assert task is not None
    assert task["payload"]["domain"] == "example.com"
    assert task["attempts"] == 1
    assert repo.claim_run_task(run_id) is None
    repo.complete_run_task(task["id"])
    assert repo.get_run_task_summary(run_id) == {
        "pending": 0, "in_progress": 0, "succeeded": 1, "failed": 0,
    }


def test_expired_lease_is_reclaimed_and_attempt_is_incremented() -> None:
    repo = _repo()
    run_id = repo.create_run("example.com", 1, 3)
    repo.enqueue_run_task(
        run_id, task_type="map_domain", idempotency_key="one", payload={"domain": "example.com"},
    )
    first = repo.claim_run_task(run_id)
    assert first is not None
    repo.conn.execute(
        "UPDATE run_tasks SET lease_until = '2000-01-01T00:00:00+00:00' WHERE id = ?",
        (first["id"],),
    )
    repo.conn.commit()
    second = repo.claim_run_task(run_id)
    assert second is not None
    assert second["id"] == first["id"]
    assert second["attempts"] == 2


def test_task_failure_retries_then_becomes_terminal() -> None:
    repo = _repo()
    run_id = repo.create_run("example.com", 1, 3)
    task_id = repo.enqueue_run_task(
        run_id, task_type="map_domain", idempotency_key="one",
        payload={"domain": "example.com"}, max_attempts=2,
    )
    first = repo.claim_run_task(run_id)
    assert first is not None
    repo.fail_run_task(task_id, "temporary", retry=True)
    assert repo.get_run_task_summary(run_id)["pending"] == 1
    second = repo.claim_run_task(run_id)
    assert second is not None
    repo.fail_run_task(task_id, "still broken", retry=True)
    assert repo.get_run_task_summary(run_id)["failed"] == 1
    assert repo.claim_run_task(run_id) is None


def test_two_connections_claim_distinct_tasks_atomically(tmp_path: Path) -> None:
    path = tmp_path / "claims.sqlite"
    first_conn = get_connection(str(path))
    init_db(first_conn)
    first_repo = GraphRepository(first_conn)
    run_id = first_repo.create_run("example.com", 1, 3)
    for domain in ("one.example", "two.example"):
        first_repo.enqueue_run_task(
            run_id, task_type="map_domain", idempotency_key=domain,
            payload={"domain": domain, "depth": 0, "parent_domain_node_id": None},
        )
    second_conn = get_connection(str(path))
    init_db(second_conn)
    second_repo = GraphRepository(second_conn)
    first_task = first_repo.claim_run_task(run_id)
    second_task = second_repo.claim_run_task(run_id)
    assert first_task is not None and second_task is not None
    assert first_task["id"] != second_task["id"]
    first_conn.close()
    second_conn.close()


class _Whois:
    async def lookup(self, domain: str) -> WhoisRecord:
        return WhoisRecord(domain=domain, raw={"source": "test-whois"})


class _Basic:
    async def lookup(self, domain: str) -> BasicIntelRecord:
        return BasicIntelRecord(domain=domain)


class _DNS:
    async def lookup(self, domain: str) -> DNSResult:
        return DNSResult(domain=domain)


class _Search:
    async def search(self, *args, **kwargs):
        return []


class _Pivots:
    async def select_pivots(self, **kwargs):
        return []


def test_crash_abandoned_task_resumes_same_run(tmp_path: Path) -> None:
    path = tmp_path / "resume.sqlite"
    conn = get_connection(str(path))
    init_db(conn)
    repo = GraphRepository(conn)
    run_id = repo.create_run("example.com", 0, 1)
    repo.get_or_create_node(run_id, "domain", "example.com", {"is_root": True})
    repo.enqueue_run_task(
        run_id, task_type="map_domain", idempotency_key="map_domain:0:example.com",
        payload={"domain": "example.com", "depth": 0, "parent_domain_node_id": None},
    )
    abandoned = repo.claim_run_task(run_id, lease_seconds=3600)
    assert abandoned is not None

    settings = Settings.from_env()
    settings.map_subdomains = False
    settings.cache_ttl_hours = 0
    settings.retry_count = 0
    orchestrator = RunOrchestrator(
        repository=repo, whois_provider=_Whois(), basic_info_provider=_Basic(),
        reverse_whois_provider=_Search(), crtsh_provider=_Search(),
        hackertarget_provider=_Search(), dns_provider=_DNS(),
        relationship_engine=_Pivots(), settings=settings,
    )
    summary = asyncio.run(orchestrator.run("example.com", max_depth=0, resume=True))
    assert summary.run_id == run_id
    assert summary.status == "completed"
    assert repo.get_run_task_summary(run_id)["succeeded"] == 1
    assert repo.count_processed_domains(run_id) == 1
