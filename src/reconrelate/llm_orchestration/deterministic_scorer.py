"""
llm_orchestration/deterministic_scorer.py

Fast, regex-based baseline pivot extraction and scoring from WHOIS and Basic Intel.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable

from reconrelate.core.normalize import normalize_identifier
from reconrelate.core.types import BasicIntelRecord, PivotCandidate, WhoisRecord
from reconrelate.core.tracker import tracker_confidence
from reconrelate.llm_orchestration.response_parser import (
    GENERIC_EMAIL_PATTERNS,
    GENERIC_NS_DOMAINS,
    is_registrar_email,
)

logger = logging.getLogger(__name__)

# A deterministic pivot at/above this score is strong enough that we skip the LLM call
# for that domain (budget gating). The model escalates only on weak/ambiguous domains.
STRONG_PIVOT_SCORE = 0.75

EMAIL_RE = re.compile(r"\b[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}\b", re.IGNORECASE)
DOMAIN_RE = re.compile(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b", re.IGNORECASE)

IGNORE_ORG_VALUE = {
    "data protected, not disclosed",
    "not available from registry",
    "redacted for privacy",
}


def _iter_strings(value: object) -> Iterable[str]:
    if value is None:
        return
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
        return
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            yield from _iter_strings(item)
        return
    yield str(value)


def extract_whois_pivot_candidates(record: WhoisRecord, root_domain: str) -> list[PivotCandidate]:
    """Deterministic regex-based extraction from WHOIS record as a reliable baseline."""
    text_parts = [chunk.strip() for chunk in _iter_strings(record.raw) if str(chunk).strip()]
    fallback = [
        record.registrant_name,
        record.registrant_org,
        record.registrant_email,
        record.registrant_phone,
        *record.nameservers,
    ]
    text_parts.extend(item.strip() for item in fallback if str(item).strip())
    text = "\n".join(text_parts)
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    candidates: list[PivotCandidate] = []

    for email in sorted(set(EMAIL_RE.findall(text))):
        email_lower = email.lower()
        if any(pattern in email_lower for pattern in GENERIC_EMAIL_PATTERNS):
            continue
        if is_registrar_email(email_lower):  # registrar/privacy email = not the owner
            continue
        candidates.append(PivotCandidate("email", normalize_identifier("email", email), 0.80, "regex: WHOIS email"))

    if record.registrant_phone.strip():
        try:
            candidates.append(PivotCandidate(
                "phone", normalize_identifier("phone", record.registrant_phone), 0.60, "whois registrant phone"
            ))
        except Exception:
            pass

    ns_domains: set[str] = set()
    for line in lines:
        low = line.lower()
        if "name server" not in low and "nserver" not in low:
            continue
        for dom in DOMAIN_RE.findall(line):
            try:
                normalized = normalize_identifier("ns", dom.lower().rstrip("."))
                if normalized == root_domain:
                    continue
                if any(generic in normalized for generic in GENERIC_NS_DOMAINS):
                    continue
                ns_domains.add(normalized)
            except Exception:
                continue
    for ns in sorted(ns_domains):
        candidates.append(PivotCandidate("ns", ns, 0.65, "regex: nameserver in WHOIS"))

    for line in lines:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key_low = key.strip().lower()
        value_clean = value.strip(" \t\n\r\"'[],")
        value_clean = " ".join(value_clean.split())
        if not value_clean or value_clean.lower() in IGNORE_ORG_VALUE or value_clean.lower() == "null":
            continue
        if "org" in key_low:
            try:
                candidates.append(PivotCandidate("org", normalize_identifier("org", value_clean), 0.75, "regex: org field"))
            except Exception:
                continue
        elif any(t in key_low for t in ("registrant", "admin", "tech", "contact", "name")):
            if "http" not in value_clean.lower():
                try:
                    candidates.append(PivotCandidate("name", normalize_identifier("name", value_clean), 0.65, "regex: contact field"))
                except Exception:
                    continue

    return candidates


def extract_deterministic_pivots(
    whois: WhoisRecord,
    basic_intel: BasicIntelRecord,
    domain: str,
) -> list[PivotCandidate]:
    """Gather all deterministic baseline candidates from WHOIS + HTML signals."""
    candidates = extract_whois_pivot_candidates(record=whois, root_domain=domain)
    for alias in basic_intel.aliases:
        candidates.append(PivotCandidate("org", alias, 0.45, "basic intel alias"))
    if basic_intel.copyright_org:
        try:
            candidates.append(PivotCandidate(
                "org", normalize_identifier("org", basic_intel.copyright_org), 0.70, "html: copyright entity"
            ))
        except Exception:
            pass
    for entity in basic_intel.legal_entities:
        try:
            candidates.append(PivotCandidate(
                "org", normalize_identifier("org", entity), 0.85,
                "html: labelled legal-page entity",
            ))
        except Exception:
            continue
    for tid in basic_intel.tracker_ids:
        try:
            candidates.append(PivotCandidate(
                "tracker", normalize_identifier("tracker", tid), tracker_confidence(tid),
                "html: site-specific analytics/tag id"
            ))
        except Exception:
            continue
    return candidates
