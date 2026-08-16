"""Typed provider-account quota snapshots returned only by explicit account checks."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderQuotaSnapshot:
    provider: str
    capability: str
    unit: str
    remaining: int
    authoritative: bool
    billing_effect: str
    checked_at: str
