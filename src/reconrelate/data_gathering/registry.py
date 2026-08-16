"""Provider registry — the seam that makes recon sources pluggable.

Every data source (WHOIS, reverse-WHOIS, subdomains, DNS, ...) registers here with
its capability, a free/paid tier, and what it needs to be usable (e.g. an API key).
The factory resolves the concrete providers it needs *from the registry* instead of
hardcoding them, so:

  * adding a source = register one class (no factory/orchestrator edits);
  * the registry can prefer a configured paid source for the same capability, but runtime policy
    activates it only for an explicitly approved BYOK run with a positive hard ceiling;
  * `reconrelate providers` can list what's active vs. what needs a key.

Providers are NOT forced into one uniform method — each capability has its own natural
call (WHOIS `.lookup`, subdomain `.search`). The registry catalogs and selects them;
the caller invokes the provider's native method. A same-capability drop-in just needs
to match that capability's method signature.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Callable, Optional

from reconrelate.core.provider_data_policy import (
    OPEN_NORMALIZED_POLICY,
    WHOXY_DATA_POLICY,
    ProviderDataPolicy,
)

FREE = "free"
PAID = "paid"


@dataclass(slots=True)
class ProviderInfo:
    capability: str                         # "whois" | "reverse_whois" | "subdomains" | "dns" | ...
    name: str                               # unique within a capability, e.g. "crtsh"
    factory: Callable[[], Any]              # builds the provider instance (no args)
    tier: str = FREE                        # FREE (works out of the box) | PAID
    requires_env: tuple[str, ...] = ()      # env vars needed before it can be used
    env_patterns: tuple[tuple[str, str], ...] = ()  # (env key, required regex search)
    description: str = ""
    operations: tuple[str, ...] = ()
    result_contract: str = "unknown"
    billable: bool = False
    billing_unit: str = "call"
    network: bool = True
    concurrency_limit: int = 4
    rate_limit_per_minute: int = 60
    max_response_bytes: int = 1_048_576
    max_result_items: int = 1_000
    max_requests_per_attempt: int = 1
    max_pages_per_attempt: int = 1
    requires_executable: str = ""
    executable_env: str = ""
    timeout_sec: float = 0.0
    source_family: str = "unclassified"
    data_policy: ProviderDataPolicy | None = None

    def __post_init__(self) -> None:
        if self.data_policy is None:
            if self.tier == PAID:
                raise ValueError(f"paid provider {self.name!r} requires an explicit data policy")
            self.data_policy = OPEN_NORMALIZED_POLICY

    def executable_path(self) -> str:
        configured = os.getenv(self.executable_env, "").strip() if self.executable_env else ""
        if configured:
            path = Path(configured).expanduser()
            usable = path.is_file() and (os.name == "nt" or os.access(path, os.X_OK))
            return str(path.resolve()) if usable else ""
        return shutil.which(self.requires_executable) or "" if self.requires_executable else ""

    def available(self) -> bool:
        """Free providers are always available; paid ones need their env vars set."""
        executable_ready = not self.requires_executable or bool(self.executable_path())
        return (
            self.name not in disabled_provider_names()
            and all(os.getenv(k) for k in self.requires_env)
            and not self.invalid_environment()
            and executable_ready
        )

    def invalid_environment(self) -> list[str]:
        invalid: list[str] = []
        for key, pattern in self.env_patterns:
            value = os.getenv(key, "")
            if value and re.search(pattern, value) is None:
                invalid.append(key)
        return invalid

    def _env_token(self) -> str:
        return re.sub(r"[^A-Z0-9]+", "_", self.name.upper()).strip("_")

    def effective_concurrency_limit(self) -> int:
        raw = os.getenv(f"RECONRELATE_PROVIDER_{self._env_token()}_CONCURRENCY", "")
        try:
            return max(1, int(raw)) if raw else max(1, self.concurrency_limit)
        except ValueError:
            return max(1, self.concurrency_limit)

    def effective_rate_limit(self) -> int:
        raw = os.getenv(f"RECONRELATE_PROVIDER_{self._env_token()}_RATE_PER_MINUTE", "")
        try:
            return max(1, int(raw)) if raw else max(1, self.rate_limit_per_minute)
        except ValueError:
            return max(1, self.rate_limit_per_minute)

    def effective_request_limit(self) -> int:
        raw = os.getenv(f"RECONRELATE_PROVIDER_{self._env_token()}_MAX_REQUESTS", "")
        try:
            return max(1, int(raw)) if raw else max(1, self.max_requests_per_attempt)
        except ValueError:
            return max(1, self.max_requests_per_attempt)

    def effective_page_limit(self) -> int:
        raw = os.getenv(f"RECONRELATE_PROVIDER_{self._env_token()}_MAX_PAGES", "")
        try:
            return max(1, int(raw)) if raw else max(1, self.max_pages_per_attempt)
        except ValueError:
            return max(1, self.max_pages_per_attempt)

    def effective_timeout(self, default: float) -> float:
        raw = os.getenv(f"RECONRELATE_PROVIDER_{self._env_token()}_TIMEOUT_SEC", "")
        try:
            configured = float(raw) if raw else float(self.timeout_sec or default)
            return max(0.01, configured)
        except ValueError:
            return max(0.01, float(self.timeout_sec or default))

    def diagnostic(self) -> dict[str, Any]:
        missing = [key for key in self.requires_env if not os.getenv(key)]
        invalid = self.invalid_environment()
        disabled = self.name in disabled_provider_names()
        missing_executables = (
            [self.requires_executable]
            if self.requires_executable and not self.executable_path() else []
        )
        return {
            "capability": self.capability,
            "name": self.name,
            "tier": self.tier,
            "available": not missing and not invalid and not missing_executables and not disabled,
            "disabled": disabled,
            "missing_environment": missing,
            "invalid_environment": invalid,
            "missing_executables": missing_executables,
            "operations": list(self.operations),
            "result_contract": self.result_contract,
            "billable": self.billable,
            "source_family": self.source_family,
            "billing_unit": self.billing_unit,
            "concurrency_limit": self.effective_concurrency_limit(),
            "rate_limit_per_minute": self.effective_rate_limit(),
            "max_response_bytes": self.max_response_bytes,
            "max_result_items": self.max_result_items,
            "max_requests_per_attempt": self.effective_request_limit(),
            "max_pages_per_attempt": self.effective_page_limit(),
            "timeout_sec": self.effective_timeout(12.0),
            "network_tested": False,
            "data_policy": self.data_policy.diagnostic(),
            "status": (
                "disabled" if disabled else
                "dependency_missing" if missing_executables else
                "configuration_invalid" if invalid else
                "ready" if not missing else "configuration_missing"
            ),
        }


def disabled_provider_names() -> set[str]:
    raw = os.getenv("RECONRELATE_DISABLE_PROVIDERS", "")
    return {value.strip().lower() for value in raw.split(",") if value.strip()}


class ProviderRegistry:
    def __init__(self) -> None:
        self._infos: list[ProviderInfo] = []
        self._cache: dict[str, Any] = {}

    def register(self, info: ProviderInfo) -> None:
        self._infos.append(info)

    def infos(self, capability: Optional[str] = None) -> list[ProviderInfo]:
        return [i for i in self._infos if capability is None or i.capability == capability]

    def available_for(self, capability: str) -> list[ProviderInfo]:
        """Usable providers for a capability, best first: paid (when configured) before free."""
        usable = [i for i in self.infos(capability) if i.available()]
        return sorted(usable, key=lambda i: 0 if i.tier == PAID else 1)

    def get(self, capability: str, name: Optional[str] = None) -> Optional[Any]:
        """Best available provider for a capability, or a specific one by name (or None)."""
        for info in self.available_for(capability):
            if name is None or info.name == name:
                return self._instance(info)
        return None

    def get_all(self, capability: str) -> list[Any]:
        return [self._instance(i) for i in self.available_for(capability)]

    def _instance(self, info: ProviderInfo) -> Any:
        key = f"{info.capability}:{info.name}"
        if key not in self._cache:
            instance = info.factory()
            try:
                setattr(instance, "__reconrelate_provider__", info.name)
                setattr(instance, "__reconrelate_billable__", info.billable)
                setattr(instance, "__reconrelate_concurrency__", info.effective_concurrency_limit())
                setattr(instance, "__reconrelate_rate_per_minute__", info.effective_rate_limit())
                setattr(instance, "__reconrelate_max_response_bytes__", max(1, info.max_response_bytes))
                setattr(instance, "__reconrelate_max_result_items__", max(1, info.max_result_items))
                setattr(instance, "__reconrelate_max_requests__", info.effective_request_limit())
                setattr(instance, "__reconrelate_max_pages__", info.effective_page_limit())
                setattr(instance, "__reconrelate_data_policy__", info.data_policy)
                if info.timeout_sec > 0:
                    setattr(instance, "__reconrelate_timeout_sec__", info.effective_timeout(12.0))
            except (AttributeError, TypeError):
                pass
            self._cache[key] = instance
        return self._cache[key]


def default_registry() -> ProviderRegistry:
    """The built-in, all-free provider set (behavior-identical to the old hardcoded wiring)."""
    # Imported lazily so registering doesn't pull heavy deps until a provider is used.
    from .basic_info_provider import BasicInfoProvider
    from .crtsh_provider import CrtshProvider
    from .dns_provider import DNSProvider
    from .hackertarget_provider import HackerTargetProvider
    from .reverse_whois_provider import ReverseWhoisProvider
    from .rdap_provider import RdapProvider
    from .subfinder_provider import SubfinderProvider
    from .whois_provider import WhoisProvider
    from .whoxy_reverse_whois_provider import WhoxyReverseWhoisProvider
    from .wikidata_acquisitions_provider import WikidataAcquisitionsProvider
    from .gleif_hierarchy_provider import GleifHierarchyProvider
    from .sec_acquisitions_provider import SecAcquisitionsProvider
    from .wayback_provider import WaybackProvider

    reg = ProviderRegistry()
    reg.register(ProviderInfo("whois", "rdap-iana", RdapProvider,
                              source_family="domain-registration-registry",
                              description="Authoritative domain registration via IANA RDAP bootstrap",
                              operations=("lookup",), result_contract="WhoisRecord",
                              concurrency_limit=4, rate_limit_per_minute=60,
                              max_response_bytes=2_097_152, max_result_items=100,
                              max_requests_per_attempt=30, max_pages_per_attempt=5))
    reg.register(ProviderInfo("whois", "python-whois", WhoisProvider,
                              source_family="domain-registration-registry",
                              description="WHOIS registrant/org/NS records (free python-whois)",
                              operations=("lookup",), result_contract="WhoisRecord",
                              concurrency_limit=2, rate_limit_per_minute=30,
                              max_response_bytes=524_288, max_result_items=100))
    reg.register(ProviderInfo("basic_info", "http-html", BasicInfoProvider,
                              source_family="current-origin-web",
                              description="Site title/description/aliases via HTTP fetch",
                              operations=("lookup",), result_contract="BasicIntelRecord",
                              concurrency_limit=8, rate_limit_per_minute=60,
                              max_response_bytes=524_288, max_result_items=50,
                              max_requests_per_attempt=12, max_pages_per_attempt=3,
                              timeout_sec=25.0))
    reg.register(ProviderInfo("reverse_whois", "duckduckgo", ReverseWhoisProvider,
                              source_family="web-search-index",
                              description="Reverse-WHOIS pivots via web search (noisy; free)",
                              operations=("search",), result_contract="list[domain]",
                              concurrency_limit=3, rate_limit_per_minute=30,
                              max_response_bytes=1_048_576, max_result_items=100))
    reg.register(ProviderInfo("reverse_whois", "whoxy", WhoxyReverseWhoisProvider,
                              source_family="commercial-whois-history",
                              tier=PAID, requires_env=("WHOXY_API_KEY",),
                              description="Reverse-WHOIS via Whoxy API (one credit per successful result page; needs WHOXY_API_KEY)",
                              operations=("search", "balance"), result_contract="list[domain] | ProviderQuotaSnapshot", billable=True,
                              billing_unit="successful result page", concurrency_limit=2,
                              rate_limit_per_minute=20, max_response_bytes=1_048_576,
                              max_result_items=2_500, max_requests_per_attempt=1,
                              max_pages_per_attempt=1, timeout_sec=20.0,
                              data_policy=WHOXY_DATA_POLICY))
    reg.register(ProviderInfo("subdomains", "subfinder", SubfinderProvider,
                              source_family="multi-upstream-wrapper",
                              description="Passive multi-source enumeration via local Subfinder binary",
                              operations=("search",), result_contract="list[SubdomainFinding]",
                              concurrency_limit=1, rate_limit_per_minute=6,
                              max_response_bytes=4_194_304, max_result_items=10_000,
                              max_requests_per_attempt=1, max_pages_per_attempt=1,
                              requires_executable="subfinder",
                              executable_env="RECONRELATE_SUBFINDER_PATH",
                              timeout_sec=25.0))
    reg.register(ProviderInfo("subdomains", "crtsh", CrtshProvider,
                              source_family="certificate-transparency",
                              description="Subdomains from Certificate Transparency (crt.sh)",
                              operations=("search",), result_contract="list[domain]",
                              concurrency_limit=2, rate_limit_per_minute=10,
                              max_response_bytes=4_194_304, max_result_items=10_000,
                              max_requests_per_attempt=6))
    reg.register(ProviderInfo("subdomains", "hackertarget", HackerTargetProvider,
                              source_family="hackertarget-aggregate",
                              description="Subdomains via HackerTarget API (fallback)",
                              operations=("search",), result_contract="list[domain]",
                              concurrency_limit=1, rate_limit_per_minute=5,
                              max_response_bytes=1_048_576, max_result_items=10_000,
                              max_requests_per_attempt=6))
    reg.register(ProviderInfo("dns", "stdlib", DNSProvider,
                              source_family="authoritative-dns",
                              description="DNS A/AAAA/MX/NS/TXT records (stdlib + optional dnspython)",
                              operations=("lookup",), result_contract="DNSResult",
                              concurrency_limit=20, rate_limit_per_minute=600,
                              max_requests_per_attempt=6, max_pages_per_attempt=6))
    reg.register(ProviderInfo("acquisitions", "wikidata", WikidataAcquisitionsProvider,
                              source_family="wikidata-community-graph",
                              description="Org parent/subsidiary/ownership relations (Wikidata, free)",
                              operations=("related_orgs",), result_contract="list[organization_relation]",
                              concurrency_limit=3, rate_limit_per_minute=120,
                              max_response_bytes=2_097_152, max_result_items=100,
                              max_requests_per_attempt=22, max_pages_per_attempt=22))
    reg.register(ProviderInfo("acquisitions", "gleif", GleifHierarchyProvider,
                              source_family="gleif-lei-register",
                              description="Exact-name LEI accounting hierarchy (GLEIF, free)",
                              operations=("related_orgs",), result_contract="list[organization_relation]",
                              concurrency_limit=2, rate_limit_per_minute=60,
                              max_response_bytes=2_097_152, max_result_items=100,
                              max_requests_per_attempt=5, max_pages_per_attempt=5,
                              timeout_sec=20.0))
    reg.register(ProviderInfo("acquisitions", "sec-edgar", SecAcquisitionsProvider,
                              source_family="sec-regulatory-filings",
                              requires_env=("RECONRELATE_SEC_USER_AGENT",),
                              env_patterns=(("RECONRELATE_SEC_USER_AGENT",
                                             r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),),
                              description="Completed acquisitions from SEC 8-K Item 2.01 filings",
                              operations=("related_orgs",), result_contract="list[organization_relation]",
                              concurrency_limit=1, rate_limit_per_minute=30,
                              max_response_bytes=2_097_152, max_result_items=20,
                              max_requests_per_attempt=7, max_pages_per_attempt=7,
                              timeout_sec=45.0))
    reg.register(ProviderInfo("historical_web", "wayback", WaybackProvider,
                              source_family="internet-archive",
                              description="Timestamped archived root-page evidence (Wayback, free)",
                              operations=("lookup",), result_contract="list[HistoricalWebRecord]",
                              concurrency_limit=1, rate_limit_per_minute=10,
                              max_response_bytes=4_194_304, max_result_items=4,
                              max_requests_per_attempt=8, max_pages_per_attempt=8,
                              timeout_sec=90.0))
    return reg
