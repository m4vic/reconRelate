"""Versioned source-lineage families used to avoid false corroboration."""

from __future__ import annotations

from collections.abc import Iterable

CATALOG_VERSION = "source-family-v1"
UNCLASSIFIED = "unclassified"

_SOURCE_FAMILIES = {
    "rdap-iana": "domain-registration-registry",
    "python-whois": "domain-registration-registry",
    "registration-cascade": "domain-registration-registry",
    "test-whois": "domain-registration-registry",
    "http-html": "current-origin-web",
    "system-dns": "authoritative-dns",
    "stdlib": "authoritative-dns",
    "crtsh": "certificate-transparency",
    "hackertarget": "hackertarget-aggregate",
    "subfinder": "multi-upstream-wrapper",
    "duckduckgo": "web-search-index",
    "search-candidate": "web-search-index",
    "whoxy": "commercial-whois-history",
    "wikidata": "wikidata-community-graph",
    "gleif": "gleif-lei-register",
    "sec-edgar": "sec-regulatory-filings",
    "wayback": "internet-archive",
    "relationship_engine": "derived-inference",
}


def source_family(source: str) -> str:
    normalized = source.strip().lower()
    if normalized in _SOURCE_FAMILIES:
        return _SOURCE_FAMILIES[normalized]
    if normalized.startswith("subfinder:"):
        upstream = normalized.split(":", 1)[1].strip()
        return f"subfinder-upstream:{upstream}" if upstream else UNCLASSIFIED
    return UNCLASSIFIED


def summarize_source_families(sources: Iterable[str]) -> dict[str, object]:
    families = sorted({source_family(source) for source in sources})
    classified = [family for family in families if family != UNCLASSIFIED]
    has_unclassified = UNCLASSIFIED in families
    if not families:
        status = "no_evidence"
    elif has_unclassified:
        status = "unclassified"
    elif len(classified) >= 2:
        status = "multiple_independent_families"
    else:
        status = "single_family"
    return {
        "catalog_version": CATALOG_VERSION,
        "families": families,
        "classified_family_count": len(classified),
        "has_unclassified_sources": has_unclassified,
        "independence_status": status,
    }
