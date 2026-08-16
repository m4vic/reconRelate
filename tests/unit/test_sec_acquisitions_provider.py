import asyncio

import pytest

from reconrelate.data_gathering.sec_acquisitions_provider import (
    SecAcquisitionsProvider,
    extract_completed_acquisitions,
)
from reconrelate.data_gathering.registry import default_registry


def _submissions(*rows: dict[str, str]) -> dict:
    keys = ("accessionNumber", "filingDate", "form", "primaryDocument", "items")
    return {"filings": {"recent": {key: [row.get(key, "") for row in rows] for key in keys}}}


class FakeSec(SecAcquisitionsProvider):
    def __init__(self, tickers: dict, submissions: dict, documents: dict[str, str]) -> None:
        self.user_agent = "Test Operator test@example.com"
        self.tickers = tickers
        self.submissions = submissions
        self.documents = documents
        self.calls: list[str] = []

    async def _get(self, url: str, *, json_response: bool) -> object:
        self.calls.append(url)
        if "company_tickers" in url:
            return self.tickers
        if "submissions" in url:
            return self.submissions
        return self.documents[url.rsplit("/", 1)[-1]]


def test_extractor_accepts_only_explicit_completed_legal_entities() -> None:
    text = (
        "On May 1, the Company completed the acquisition of Example Systems, Inc. "
        "The Company entered into an agreement to acquire Future Product assets. "
        "The Company completed the disposition of Old Business LLC."
    )
    assert [item[0] for item in extract_completed_acquisitions(text)] == ["Example Systems, Inc"]


def test_extractor_requires_both_named_agreement_and_cross_referenced_completion() -> None:
    agreement = "The Company agreed to acquire Trinity Group Construction, Inc."
    completion = "The Company completed the Acquisition described in Item 1.01 above."
    assert extract_completed_acquisitions(agreement) == []
    rows = extract_completed_acquisitions(f"{agreement} {completion}")
    assert [row[0] for row in rows] == ["Trinity Group Construction, Inc"]
    assert "completed the Acquisition" in rows[0][1]


def test_provider_resolves_exact_filer_and_returns_primary_filing_provenance() -> None:
    filing = {
        "accessionNumber": "0000123456-26-000001", "filingDate": "2026-01-02",
        "form": "8-K", "primaryDocument": "deal.htm", "items": "2.01,9.01",
    }
    provider = FakeSec(
        {"0": {"cik_str": 123456, "title": "EXAMPLE PUBLIC CORP"}},
        _submissions(filing),
        {"deal.htm": "<p>The Company consummated the acquisition of Target Labs LLC.</p>"},
    )
    rows = asyncio.run(provider.related_orgs("Example Public Corp"))
    assert len(rows) == 1
    assert rows[0]["relation"] == "acquired"
    assert rows[0]["org"] == "Target Labs LLC"
    assert rows[0]["cik"] == "0000123456"
    assert rows[0]["source_record_id"] == filing["accessionNumber"]
    assert rows[0]["filing_url"].endswith("/deal.htm")
    assert "consummated the acquisition" in rows[0]["supporting_text"]


def test_ambiguous_or_fuzzy_filer_name_abstains_before_submissions() -> None:
    duplicate = {
        "0": {"cik_str": 1, "title": "SAME CORP"},
        "1": {"cik_str": 2, "title": "Same Corp"},
    }
    ambiguous = FakeSec(duplicate, {}, {})
    fuzzy = FakeSec({"0": {"cik_str": 1, "title": "EXAMPLE CORPORATION"}}, {}, {})
    assert asyncio.run(ambiguous.related_orgs("Same Corp")) == []
    assert asyncio.run(fuzzy.related_orgs("Example Corp")) == []
    assert len(ambiguous.calls) == len(fuzzy.calls) == 1


def test_only_recent_item_201_filings_are_fetched() -> None:
    rows = (
        {"accessionNumber": "A", "filingDate": "2026-01-01", "form": "8-K",
         "primaryDocument": "proposal.htm", "items": "1.01"},
        {"accessionNumber": "B", "filingDate": "2026-01-02", "form": "10-K",
         "primaryDocument": "annual.htm", "items": "2.01"},
        {"accessionNumber": "C", "filingDate": "2026-01-03", "form": "8-K/A",
         "primaryDocument": "completed.htm", "items": "2.01, 9.01"},
    )
    provider = FakeSec(
        {"0": {"cik_str": 3, "title": "FILER INC"}}, _submissions(*rows),
        {"completed.htm": "The Company completed the acquisition of Target Company Ltd."},
    )
    result = asyncio.run(provider.related_orgs("Filer Inc"))
    assert [row["org"] for row in result] == ["Target Company Ltd"]
    assert sum("Archives" in call for call in provider.calls) == 1


def test_zero_result_limit_makes_no_requests() -> None:
    provider = FakeSec({}, {}, {})
    assert asyncio.run(provider.related_orgs("Anything", max_results=0)) == []
    assert provider.calls == []


def test_registry_rejects_undeclared_sec_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECONRELATE_SEC_USER_AGENT", "ReconRelate/0.1")
    info = next(item for item in default_registry().infos("acquisitions") if item.name == "sec-edgar")
    assert info.available() is False
    assert info.diagnostic()["status"] == "configuration_invalid"
    assert info.diagnostic()["invalid_environment"] == ["RECONRELATE_SEC_USER_AGENT"]


def test_registry_accepts_contact_bearing_sec_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECONRELATE_SEC_USER_AGENT", "Example Research security@example.org")
    info = next(item for item in default_registry().infos("acquisitions") if item.name == "sec-edgar")
    assert info.available() is True
