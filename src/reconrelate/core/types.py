from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# "tracker" = a web analytics/tag id (GA/GA4/GTM/AdSense). Two domains sharing one are
# almost always the same operator — a strong, deterministic cross-domain relationship signal.
ALLOWED_IDENTIFIER_TYPES = {"email", "org", "name", "phone", "ns", "tracker"}


@dataclass(slots=True)
class WhoisRecord:
    domain: str
    registrant_name: str = ""
    registrant_org: str = ""
    registrant_email: str = ""
    registrant_phone: str = ""
    nameservers: list[str] = field(default_factory=list)
    creation_date: str = ""
    expiration_date: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SubdomainFinding:
    domain: str
    sources: list[str] = field(default_factory=list)


@dataclass(slots=True)
class HistoricalWebRecord:
    domain: str
    captured_at: str
    original_url: str
    archive_url: str
    digest: str = ""
    title: str = ""
    tracker_ids: list[str] = field(default_factory=list)
    copyright_org: str = ""


@dataclass(slots=True)
class BasicIntelRecord:
    domain: str
    title: str = ""
    description: str = ""
    aliases: list[str] = field(default_factory=list)
    tracker_ids: list[str] = field(default_factory=list)  # GA/GA4/GTM/AdSense ids from the page
    copyright_org: str = ""                                # legal entity from the footer copyright
    final_url: str = ""
    redirect_domain: str = ""
    legal_entities: list[str] = field(default_factory=list)
    legal_entity_sources: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TrackerVerification:
    domain: str
    tracker_id: str
    matched: bool
    final_url: str = ""


@dataclass(slots=True)
class Identifier:
    id_type: str
    value: str


@dataclass(slots=True)
class PivotCandidate:
    id_type: str
    value: str
    score: float
    reason: str


@dataclass(slots=True)
class RunSummary:
    run_id: str
    status: str
    root_domain: str
    domains_count: int
    identifiers_count: int
    edges_count: int
