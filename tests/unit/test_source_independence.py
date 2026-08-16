from reconrelate.core.source_independence import (
    CATALOG_VERSION,
    source_family,
    summarize_source_families,
)


def test_rdap_and_legacy_whois_are_one_upstream_family() -> None:
    summary = summarize_source_families(["rdap-iana", "python-whois"])

    assert summary["families"] == ["domain-registration-registry"]
    assert summary["classified_family_count"] == 1
    assert summary["independence_status"] == "single_family"
    assert summary["catalog_version"] == CATALOG_VERSION


def test_primary_web_and_regulatory_filing_are_independent_families() -> None:
    summary = summarize_source_families(["http-html", "sec-edgar"])

    assert summary["classified_family_count"] == 2
    assert summary["independence_status"] == "multiple_independent_families"


def test_unknown_source_is_not_assumed_independent() -> None:
    summary = summarize_source_families(["mystery-a", "mystery-b"])

    assert summary["families"] == ["unclassified"]
    assert summary["classified_family_count"] == 0
    assert summary["independence_status"] == "unclassified"


def test_subfinder_retains_its_named_upstream_family() -> None:
    assert source_family("subfinder:alienvault") == "subfinder-upstream:alienvault"
