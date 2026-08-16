import asyncio
import json
import sys
from pathlib import Path

import pytest

from reconrelate.config.settings import Settings
from reconrelate.core.errors import ProviderMalformedError, ProviderTimeoutError
from reconrelate.core.provider_budget import provider_budget
from reconrelate.core.provider_result import ProviderResult, observations_from_result
from reconrelate.core.types import SubdomainFinding
from reconrelate.data_gathering.registry import default_registry
from reconrelate.data_gathering.subfinder_provider import SubfinderProvider, _sources_from_env
from reconrelate.db.db import get_connection, init_db
from reconrelate.db.repositories import GraphRepository
from reconrelate.orchestrator.orchestrator import RunOrchestrator


def _jsonl(*items: dict) -> bytes:
    return b"\n".join(json.dumps(item).encode() for item in items)


def test_jsonl_parser_merges_sources_filters_scope_and_caps_results() -> None:
    output = _jsonl(
        {"host": "A.Example.com", "source": "crtsh"},
        {"host": "a.example.com", "sources": ["alienvault", "crtsh"]},
        {"host": "outside.example.net", "source": "crtsh"},
        {"host": "b.example.com", "source": "commoncrawl"},
    )
    assert SubfinderProvider._parse(output, "example.com", 1) == [
        SubdomainFinding("a.example.com", ["alienvault", "crtsh"]),
    ]


def test_jsonl_parser_rejects_malformed_lines() -> None:
    with pytest.raises(ProviderMalformedError, match="line 1"):
        SubfinderProvider._parse(b"not-json\n", "example.com", 10)


def test_source_policy_is_explicit_and_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RECONRELATE_SUBFINDER_SOURCES", raising=False)
    assert _sources_from_env() == ["crtsh", "alienvault", "commoncrawl", "waybackarchive"]
    monkeypatch.setenv("RECONRELATE_SUBFINDER_SOURCES", "crtsh,securitytrails")
    assert _sources_from_env() == ["crtsh", "securitytrails"]
    monkeypatch.setenv("RECONRELATE_SUBFINDER_SOURCES", "crtsh,$shell")
    with pytest.raises(ProviderMalformedError):
        _sources_from_env()


def test_manifest_reports_missing_or_configured_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RECONRELATE_SUBFINDER_PATH", raising=False)
    monkeypatch.setattr("reconrelate.data_gathering.registry.shutil.which", lambda name: None)
    info = next(item for item in default_registry().infos() if item.name == "subfinder")
    assert info.diagnostic()["status"] == "dependency_missing"
    executable = tmp_path / "subfinder.exe"
    executable.write_bytes(b"fixture")
    executable.chmod(0o755)
    monkeypatch.setenv("RECONRELATE_SUBFINDER_PATH", str(executable))
    diagnostic = info.diagnostic()
    assert diagnostic["available"] is True
    assert diagnostic["missing_executables"] == []


class _FakeProcess:
    def __init__(self, stdout: bytes, *, hangs: bool = False) -> None:
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.returncode = None
        self.terminated = False
        self._hangs = hangs
        self._done = asyncio.Event()
        self.stdout.feed_data(stdout)
        self.stderr.feed_data(b"")
        if not hangs:
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            self.returncode = 0
            self._done.set()

    async def wait(self) -> int:
        await self._done.wait()
        return int(self.returncode or 0)

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        self._done.set()

    def kill(self) -> None:
        self.terminate()


def test_runner_uses_passive_bounded_flags_and_accounts_one_opaque_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = []

    async def spawn(*args, **kwargs):
        captured.extend(args)
        return _FakeProcess(_jsonl({
            "host": "api.example.com", "sources": ["crtsh", "alienvault"],
        }))

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    provider = SubfinderProvider(sys.executable)
    with provider_budget(max_requests=1, max_pages=1) as budget:
        findings = asyncio.run(provider.search("example.com", max_results=10))
    assert findings == [SubdomainFinding("api.example.com", ["alienvault", "crtsh"])]
    assert "-json" in captured and "-collect-sources" in captured
    assert "-disable-update-check" in captured
    assert "-active" not in captured and "-all" not in captured
    assert (budget.requests, budget.pages) == (1, 1)


def test_runner_terminates_hung_process(monkeypatch: pytest.MonkeyPatch) -> None:
    processes = []

    async def spawn(*args, **kwargs):
        process = _FakeProcess(b"", hangs=True)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    provider = SubfinderProvider(sys.executable, wall_timeout_sec=0.02)
    with pytest.raises(ProviderTimeoutError):
        asyncio.run(provider.search("example.com"))
    assert processes[0].terminated


def test_runner_cancellation_terminates_process(monkeypatch: pytest.MonkeyPatch) -> None:
    processes = []

    async def spawn(*args, **kwargs):
        process = _FakeProcess(b"", hangs=True)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    provider = SubfinderProvider(sys.executable, wall_timeout_sec=5)

    async def scenario() -> None:
        task = asyncio.create_task(provider.search("example.com"))
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert processes[0].terminated


def test_per_source_findings_create_independent_observations() -> None:
    result = ProviderResult.from_data(
        "subfinder", "subdomains",
        [SubdomainFinding("api.example.com", ["alienvault", "crtsh"])],
        subject="example.com",
    )
    observations = observations_from_result(result)
    assert {item.source for item in observations} == {
        "subfinder/alienvault", "subfinder/crtsh",
    }


def test_subfinder_failure_falls_back_to_builtin_provider() -> None:
    class BrokenFinder:
        __reconrelate_provider__ = "subfinder"

        async def search(self, domain: str, max_results: int = 15):
            raise RuntimeError("subfinder unavailable")

    class CrtFallback:
        __reconrelate_provider__ = "crtsh"

        async def search(self, domain: str, max_results: int = 15):
            return ["api.example.com"]

    conn = get_connection(":memory:")
    init_db(conn)
    repo = GraphRepository(conn)
    settings = Settings.from_env()
    settings.retry_count = 0
    orchestrator = RunOrchestrator(
        repository=repo, whois_provider=None, basic_info_provider=None,
        reverse_whois_provider=None, crtsh_provider=CrtFallback(), hackertarget_provider=None,
        dns_provider=None, relationship_engine=object(), settings=settings,
        subfinder_provider=BrokenFinder(),
    )
    run_id = repo.create_run("example.com", 0, 1)
    result = asyncio.run(orchestrator._fetch_subdomains("example.com", run_id))
    assert result.provider == "crtsh"
    assert result.data == ["api.example.com"]


def test_full_run_links_all_subfinder_sources_to_one_claim() -> None:
    class Finder:
        __reconrelate_provider__ = "subfinder"

        async def search(self, domain: str, max_results: int = 15):
            return [SubdomainFinding("api.example.com", ["alienvault", "crtsh"])]

    class NoPivots:
        async def select_pivots(self, **kwargs):
            return []

    conn = get_connection(":memory:")
    init_db(conn)
    repo = GraphRepository(conn)
    settings = Settings.from_env()
    settings.map_subdomains = True
    settings.cache_ttl_hours = 0
    orchestrator = RunOrchestrator(
        repository=repo, whois_provider=None, basic_info_provider=None,
        reverse_whois_provider=None, crtsh_provider=None, hackertarget_provider=None,
        dns_provider=None, relationship_engine=NoPivots(), settings=settings,
        subfinder_provider=Finder(),
    )
    summary = asyncio.run(orchestrator.run("example.com", max_depth=1))
    graph = repo.get_run_graph(summary.run_id)
    claim = next(item for item in graph["claims"] if item["claim_type"] == "domain_has_subdomain")
    assert {item["source"] for item in claim["evidence"]} == {
        "subfinder/alienvault", "subfinder/crtsh",
    }
