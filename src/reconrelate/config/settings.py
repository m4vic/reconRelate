from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# Crawl-size tiers: how far/wide the graph expands. Opt-in — unset keeps the classic
# defaults. `low` doubles as a cheap "scout" to gauge an org's fan-out before going deep.
_BUDGET_PRESETS: dict[str, dict[str, int]] = {
    "low":    {"max_depth": 1,  "global_max_nodes": 50,   "pivot_top_k": 3, "max_domains_per_identifier": 3},
    "medium": {"max_depth": 2,  "global_max_nodes": 250,  "pivot_top_k": 5, "max_domains_per_identifier": 5},
    "max":    {"max_depth": -1, "global_max_nodes": 2000, "pivot_top_k": 8, "max_domains_per_identifier": 10},
}


_RUN_MODE_PRESETS: dict[str, dict[str, int | bool]] = {
    "quick": {
        "prefer_fast_subdomain": True,
        "max_subdomains_fetched": 30,
        "max_subdomain_graph_nodes": 40,
        "max_pending_queue": 300,
        "subdomain_enum_max_depth": 0,
    },
    "deep": {
        "prefer_fast_subdomain": False,
        "max_subdomains_fetched": 120,
        "max_subdomain_graph_nodes": 200,
        "max_pending_queue": 5000,
        "subdomain_enum_max_depth": 2,
    },
}


@dataclass(slots=True)
class Settings:
    db_path: str
    default_max_depth: int
    pivot_top_k: int
    pivot_score_threshold: float
    max_domains_per_identifier: int
    global_max_nodes: int
    request_timeout_sec: int
    retry_count: int
    openai_api_key: str
    llm_model: str
    run_mode: str
    prefer_fast_subdomain_source: bool
    max_subdomains_fetched: int
    max_subdomain_graph_nodes_per_domain: int
    max_pending_queue: int
    subdomain_enumeration_max_depth: int
    ollama_api_base: str
    llm_timeout_sec: int
    per_domain_timeout_sec: int
    fast_model: str
    auto_save_artifacts: bool
    artifacts_dir: str
    llm_allow_cloud: bool
    llm_escalate_only: bool
    expand_acquisitions: bool
    historical_web: bool
    # Async concurrency limits (replace old fixed-size ThreadPoolExecutor pools).
    concurrency_gather: int
    concurrency_pivot: int
    # Provider tier: controls which providers auto-activate (future Phase 4).
    provider_tier: str
    max_provider_calls: int
    max_billable_units: float
    paid_approved: bool
    max_model_calls: int
    max_model_input_tokens: int
    max_model_output_tokens: int
    max_cloud_tokens: int
    max_cloud_cost_usd: float
    cloud_approved: bool
    # Cross-run scrape cache: reuse a domain's stored mapping if scraped within this many
    # hours (0 or negative = disabled → always re-scrape). Default 7 days.
    cache_ttl_hours: int = 168
    # ReconRelate maps related *root domains*, not subdomains (a downstream step handled by
    # dedicated tools like subfinder). Off by default; opt in with RECONRELATE_MAP_SUBDOMAINS.
    map_subdomains: bool = False
    # Maximum time a call waits in the shared FIFO provider admission queue.
    provider_capacity_wait_sec: float = 5.0

    @classmethod
    def from_env(cls, run_mode_cli: str | None = None, budget_cli: str | None = None) -> "Settings":
        default_db_path = str(Path(tempfile.gettempdir()) / "reconrelate" / "reconrelate.db")
        mode_raw = (run_mode_cli or os.getenv("RECONRELATE_RUN_MODE") or "deep").strip().lower()
        if mode_raw not in _RUN_MODE_PRESETS:
            mode_raw = "deep"
        preset = _RUN_MODE_PRESETS[mode_raw]

        # Crawl-size tier (opt-in): supplies defaults for the depth/breadth caps below;
        # a specific env var still overrides. Unset => classic defaults unchanged.
        budget_raw = (budget_cli or os.getenv("RECONRELATE_BUDGET") or "").strip().lower()
        budget = _BUDGET_PRESETS.get(budget_raw, {})

        prefer_fast = bool(preset["prefer_fast_subdomain"])
        prefer_fast = _env_bool("RECONRELATE_PREFER_FAST_SUBDOMAIN", prefer_fast)

        max_sub_fetched = _env_int(
            "RECONRELATE_MAX_SUBDOMAINS_FETCHED",
            int(preset["max_subdomains_fetched"]),
        )
        max_sub_graph = _env_int(
            "RECONRELATE_MAX_SUBDOMAIN_GRAPH_NODES",
            int(preset["max_subdomain_graph_nodes"]),
        )
        max_pending = _env_int("RECONRELATE_MAX_PENDING_QUEUE", int(preset["max_pending_queue"]))
        sub_enum_depth = _env_int(
            "RECONRELATE_SUBDOMAIN_ENUM_MAX_DEPTH",
            int(preset["subdomain_enum_max_depth"]),
        )

        return cls(
            db_path=os.getenv("RECONRELATE_DB_PATH", default_db_path),
            # -1 = no BFS depth cap (run until queue empty / GLOBAL_MAX_NODES); >=0 caps depth.
            # Budget tier (if set) supplies the default; a specific env var still wins.
            default_max_depth=_env_int("DEFAULT_MAX_DEPTH", budget.get("max_depth", -1)),
            pivot_top_k=_env_int("PIVOT_TOP_K", budget.get("pivot_top_k", 5)),
            pivot_score_threshold=_env_float("PIVOT_SCORE_THRESHOLD", 0.40),
            max_domains_per_identifier=_env_int("MAX_DOMAINS_PER_IDENTIFIER", budget.get("max_domains_per_identifier", 3)),
            global_max_nodes=_env_int("GLOBAL_MAX_NODES", budget.get("global_max_nodes", 500)),
            request_timeout_sec=_env_int("REQUEST_TIMEOUT_SEC", 12),
            retry_count=_env_int("RETRY_COUNT", 2),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            llm_model=os.getenv("LLM_MODEL", "").strip(),
            run_mode=mode_raw,
            prefer_fast_subdomain_source=prefer_fast,
            max_subdomains_fetched=max(1, max_sub_fetched),
            max_subdomain_graph_nodes_per_domain=max(1, max_sub_graph),
            max_pending_queue=max(0, max_pending),
            subdomain_enumeration_max_depth=sub_enum_depth,
            ollama_api_base=os.getenv("OLLAMA_API_BASE", "http://localhost:11434").rstrip("/"),
            llm_timeout_sec=max(10, _env_int("LLM_TIMEOUT_SEC", 120)),
            per_domain_timeout_sec=max(30, _env_int("PER_DOMAIN_TIMEOUT_SEC", 180)),
            fast_model=os.getenv("FAST_LLM_MODEL", "").strip(),
            auto_save_artifacts=_env_bool("RECONRELATE_AUTO_SAVE_ARTIFACTS", True),
            artifacts_dir=os.getenv("RECONRELATE_ARTIFACTS_DIR", "artifacts").strip() or "artifacts",
            llm_allow_cloud=_env_bool("RECONRELATE_LLM_ALLOW_CLOUD", False),
            # Budget gating: skip the per-domain LLM call when a strong deterministic pivot exists.
            llm_escalate_only=_env_bool("RECONRELATE_LLM_ESCALATE_ONLY", True),
            # Expand org pivots via Wikidata acquisition/ownership edges (off by default).
            expand_acquisitions=_env_bool("RECONRELATE_EXPAND_ACQUISITIONS", False),
            historical_web=_env_bool("RECONRELATE_HISTORICAL_WEB", False),
            concurrency_gather=_env_int("RECONRELATE_CONCURRENCY_GATHER", 10),
            concurrency_pivot=_env_int("RECONRELATE_CONCURRENCY_PIVOT", 15),
            provider_tier=os.getenv("RECONRELATE_PROVIDER_TIER", "free").strip().lower(),
            max_provider_calls=max(0, _env_int("RECONRELATE_MAX_PROVIDER_CALLS", 500)),
            max_billable_units=max(0.0, _env_float("RECONRELATE_MAX_BILLABLE_UNITS", 0.0)),
            paid_approved=False,
            max_model_calls=max(0, _env_int("RECONRELATE_MAX_MODEL_CALLS", 50)),
            max_model_input_tokens=max(0, _env_int("RECONRELATE_MAX_MODEL_INPUT_TOKENS", 200_000)),
            max_model_output_tokens=max(0, _env_int("RECONRELATE_MAX_MODEL_OUTPUT_TOKENS", 25_600)),
            max_cloud_tokens=max(0, _env_int("RECONRELATE_MAX_CLOUD_TOKENS", 0)),
            max_cloud_cost_usd=max(0.0, _env_float("RECONRELATE_MAX_CLOUD_COST_USD", 0.0)),
            cloud_approved=False,
            cache_ttl_hours=_env_int("RECONRELATE_CACHE_TTL_HOURS", 168),
            map_subdomains=_env_bool("RECONRELATE_MAP_SUBDOMAINS", False),
            provider_capacity_wait_sec=max(
                0.0, _env_float("RECONRELATE_PROVIDER_CAPACITY_WAIT_SEC", 5.0)
            ),
        )
