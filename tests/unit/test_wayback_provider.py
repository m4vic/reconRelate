import asyncio

import pytest

from reconrelate.core.errors import ProviderMalformedError
from reconrelate.core.types import HistoricalWebRecord
from reconrelate.core.provider_result import ProviderResult, observations_from_result
from reconrelate.data_gathering.wayback_provider import (
    WaybackProvider,
    parse_cdx,
    record_from_html,
)
from reconrelate.config.settings import Settings
from reconrelate.orchestrator.orchestrator import RunOrchestrator


HEADER = ["timestamp", "original", "mimetype", "statuscode", "digest"]


def _row(timestamp: str, digest: str, original: str = "https://example.com/") -> dict[str, str]:
    return {"timestamp": timestamp, "original": original, "mimetype": "text/html",
            "statuscode": "200", "digest": digest}


def test_parse_cdx_filters_non_html_and_unsuccessful_rows() -> None:
    payload = [HEADER,
               ["20200101000000", "https://example.com/", "text/html", "200", "A"],
               ["20200102000000", "https://example.com/a.png", "image/png", "200", "B"],
               ["20200103000000", "https://example.com/", "text/html", "301", "C"]]
    assert [row["digest"] for row in parse_cdx(payload)] == ["A"]


def test_parse_cdx_rejects_missing_contract_fields() -> None:
    with pytest.raises(ProviderMalformedError):
        parse_cdx([["timestamp", "original"], ["2020", "https://example.com/"]])


def test_record_extracts_time_scoped_title_tracker_and_copyright() -> None:
    record = record_from_html(
        "example.com", _row("20200102030405", "DIGEST"),
        "<title> Old Example </title><script>G-ABCDEF12</script>"
        "Copyright 2020 Example Holdings, Inc. All rights reserved",
    )
    assert record is not None
    assert record.captured_at == "2020-01-02T03:04:05+00:00"
    assert record.title == "Old Example"
    assert record.tracker_ids == ["G-ABCDEF12"]
    assert record.copyright_org == "Example Holdings, Inc"
    assert record.archive_url.endswith("/20200102030405id_/https://example.com/")


def test_record_rejects_capture_for_another_host() -> None:
    assert record_from_html(
        "example.com", _row("20200102030405", "A", "https://attacker.test/"), "<title>x</title>"
    ) is None


class FakeWayback(WaybackProvider):
    def __init__(self) -> None:
        self.queries: list[tuple[str, int]] = []

    async def _query(self, target_url: str, limit: int) -> list[dict[str, str]]:
        self.queries.append((target_url, limit))
        if limit > 0:
            return [_row("20000101000000", "EARLY", target_url)]
        return [_row("20250101000000", "LATE", target_url)]

    async def _snapshot(self, domain: str, row: dict[str, str]) -> HistoricalWebRecord | None:
        return record_from_html(domain, row, "<title>Archived</title>")


def test_lookup_queries_exact_bare_and_www_roots_and_keeps_history_ends() -> None:
    provider = FakeWayback()
    records = asyncio.run(provider.lookup("example.com", max_results=2))
    assert provider.queries == [
        ("https://example.com/", 2), ("https://example.com/", -2),
        ("https://www.example.com/", 2), ("https://www.example.com/", -2),
    ]
    assert [record.digest for record in records] == ["EARLY", "LATE"]


def test_one_result_does_not_duplicate_slice() -> None:
    records = asyncio.run(FakeWayback().lookup("example.com", max_results=1))
    assert len(records) == 1


def test_historical_records_become_time_scoped_non_current_observations() -> None:
    record = HistoricalWebRecord(
        domain="example.com", captured_at="2020-01-02T03:04:05+00:00",
        original_url="https://example.com/",
        archive_url="https://web.archive.org/web/20200102030405id_/https://example.com/",
        digest="DIGEST", title="Old title", tracker_ids=["G-ABCDEF12"],
        copyright_org="Example Holdings, Inc",
    )
    result = ProviderResult.from_data("wayback", "historical_web", [record], subject="example.com")
    observations = observations_from_result(result)
    assert {item.predicate for item in observations} == {
        "has_archived_snapshot", "had_page_title", "historically_used_tracker",
        "historically_claimed_copyright_org",
    }
    assert all(item.observed_at == record.captured_at for item in observations)
    assert all(item.valid_from == record.captured_at for item in observations)
    assert all(item.normalized["historical"] is True for item in observations)
    assert not any(item.predicate in {"uses_tracker", "claims_copyright_org"} for item in observations)


def test_normal_gather_is_opt_in_and_returns_historical_provider_result() -> None:
    settings = Settings.from_env()
    settings.retry_count = 0
    provider = FakeWayback()
    setattr(provider, "__reconrelate_provider__", "wayback")
    orchestrator = RunOrchestrator(
        repository=object(), whois_provider=None, basic_info_provider=None,
        reverse_whois_provider=None, crtsh_provider=None, hackertarget_provider=None,
        dns_provider=None, relationship_engine=None, settings=settings,
        historical_web_provider=provider,
    )
    disabled = asyncio.run(orchestrator._gather_all_data("example.com", False, "run"))[-1]
    assert disabled.status == "empty"
    assert provider.queries == []

    settings.historical_web = True
    enabled = asyncio.run(orchestrator._gather_all_data("example.com", False, "run"))[-1]
    assert enabled.provider == "wayback"
    assert enabled.status == "success"
    assert len(enabled.data) == 2
