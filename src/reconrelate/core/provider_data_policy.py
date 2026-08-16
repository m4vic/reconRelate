"""Executable storage and export policy attached to every provider adapter."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


POLICY_VERSION = "provider-data-use-v1"
_RAW_RETENTION = {"none", "hash_only"}
_NORMALIZED_RETENTION = {"run", "project"}
_EXPORT_SCOPES = {"none", "derived_only", "normalized"}


@dataclass(frozen=True, slots=True)
class ProviderDataPolicy:
    raw_retention: str = "hash_only"
    normalized_retention: str = "project"
    cross_run_cache: bool = True
    export_scope: str = "normalized"
    version: str = POLICY_VERSION
    terms_url: str = ""
    reviewed_at: str = ""

    def __post_init__(self) -> None:
        if self.raw_retention not in _RAW_RETENTION:
            raise ValueError(f"invalid raw retention policy: {self.raw_retention}")
        if self.normalized_retention not in _NORMALIZED_RETENTION:
            raise ValueError(f"invalid normalized retention policy: {self.normalized_retention}")
        if self.export_scope not in _EXPORT_SCOPES:
            raise ValueError(f"invalid export scope: {self.export_scope}")
        if not self.version.strip():
            raise ValueError("provider data policy version is required")
        if self.cross_run_cache and self.normalized_retention != "project":
            raise ValueError("cross-run caching requires project-level normalized retention")

    def diagnostic(self) -> dict[str, Any]:
        return asdict(self)

    def observation_fields(self) -> dict[str, Any]:
        return {
            "data_policy_version": self.version,
            "cache_allowed": self.cross_run_cache,
            "export_scope": self.export_scope,
            "raw_retention": self.raw_retention,
        }


OPEN_NORMALIZED_POLICY = ProviderDataPolicy()
WHOXY_DATA_POLICY = ProviderDataPolicy(
    raw_retention="hash_only",
    normalized_retention="run",
    cross_run_cache=False,
    export_scope="derived_only",
    terms_url="https://www.whoxy.com/terms.php",
    reviewed_at="2026-08-14",
)


def provider_data_policy(provider: object) -> ProviderDataPolicy:
    value = getattr(provider, "__reconrelate_data_policy__", OPEN_NORMALIZED_POLICY)
    return value if isinstance(value, ProviderDataPolicy) else OPEN_NORMALIZED_POLICY


def observation_policy_fields(provider: object) -> dict[str, Any]:
    return provider_data_policy(provider).observation_fields()
