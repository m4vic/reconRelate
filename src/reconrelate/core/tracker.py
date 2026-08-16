"""Deterministic tracker-ID quality policy shared by extraction, scoring, and evidence."""

from __future__ import annotations

import re


def is_plausible_tracker(value: str) -> bool:
    normalized = value.strip().upper()
    payload = normalized.split("-", 1)[-1].replace("-", "")
    if any(marker in payload for marker in ("XXXXX", "TEST", "DEMO", "SAMPLE")):
        return False
    alnum = re.sub(r"[^A-Z0-9]", "", payload)
    if not alnum or set(alnum) <= {"0"} or len(set(alnum)) <= 2:
        return False
    return True


def tracker_confidence(value: str) -> float:
    """Relative identifier specificity, before cross-domain verification."""
    normalized = value.strip().upper()
    if normalized.startswith("CA-PUB-"):
        return 0.95
    if normalized.startswith("UA-"):
        return 0.92
    if normalized.startswith("GTM-"):
        return 0.90
    if normalized.startswith("G-"):
        return 0.85
    return 0.70
