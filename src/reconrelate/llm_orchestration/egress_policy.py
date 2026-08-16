"""Deterministic evidence projections for local and cloud model calls."""

from __future__ import annotations

import re
from typing import Any

CLOUD_EGRESS_POLICY_VERSION = "cloud-redacted-v1"
LOCAL_EGRESS_POLICY_VERSION = "local-structured-v1"

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MAX_STRING = 1_000
_MAX_LIST = 250


def _text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = _CONTROL_CHARS.sub("", str(value)).strip()
    return normalized[:_MAX_STRING] or None


def _list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    items = sorted(value, key=str) if isinstance(value, set) else list(value)
    for item in items[:_MAX_LIST]:
        normalized = _text(item)
        if normalized is not None:
            result.append(normalized)
    return result


def prepare_model_evidence(evidence: dict, *, cloud: bool) -> dict:
    """Return a bounded allowlisted copy; cloud output excludes personal WHOIS fields."""
    whois = evidence.get("whois") if isinstance(evidence.get("whois"), dict) else {}
    intel = evidence.get("basic_intel") if isinstance(evidence.get("basic_intel"), dict) else {}
    projected_whois: dict[str, Any] = {
        "registrant_org": _text(whois.get("registrant_org")),
        "nameservers": _list(whois.get("nameservers")),
        "creation_date": _text(whois.get("creation_date")),
        "expiration_date": _text(whois.get("expiration_date")),
    }
    if not cloud:
        projected_whois.update({
            "registrant_name": _text(whois.get("registrant_name")),
            "registrant_email": _text(whois.get("registrant_email")),
            "registrant_phone": _text(whois.get("registrant_phone")),
        })
    return {
        "_egress_policy": (
            CLOUD_EGRESS_POLICY_VERSION if cloud else LOCAL_EGRESS_POLICY_VERSION
        ),
        "domain": _text(evidence.get("domain")),
        "whois": projected_whois,
        "basic_intel": {
            "title": _text(intel.get("title")),
            "description": _text(intel.get("description")),
            "aliases": _list(intel.get("aliases")),
            "copyright_org": _text(intel.get("copyright_org")),
            "tracker_ids": _list(intel.get("tracker_ids")),
            "redirect_domain": _text(intel.get("redirect_domain")),
            "legal_entities": _list(intel.get("legal_entities")),
        },
        "subdomains": _list(evidence.get("subdomains")),
        "subdomains_truncated": (
            max(0, int(evidence["subdomains_truncated"]))
            if isinstance(evidence.get("subdomains_truncated"), int) else None
        ),
    }
