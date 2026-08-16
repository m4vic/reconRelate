from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from typing import Callable

from reconrelate.config.settings import Settings
from reconrelate.core.errors import SecurityError
from reconrelate.orchestrator.orchestrator import RunOrchestrator
from reconrelate.data_gathering.registry import ProviderRegistry, default_registry
from reconrelate.db.db import get_connection, init_db, restrict_sqlite_file_permissions
from reconrelate.db.repositories import GraphRepository
from reconrelate.llm_orchestration.relationship_engine import (
    LLMClient,
    RelationshipEngine,
    _litellm_model_id,
    is_cloud_model,
)
from reconrelate.llm_orchestration.model_budget import ModelBudget


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Runtime:
    repository: GraphRepository
    orchestrator: RunOrchestrator
    close: Callable[[], None]


def _allowed(provider: object, allow_billable: bool) -> bool:
    return allow_billable or not bool(getattr(provider, "__reconrelate_billable__", False))


def _pick(reg: ProviderRegistry, capability: str, *, allow_billable: bool = True):
    """Resolve a single-provider capability, honoring a `source.<cap>` pin from config/env.

    ``RECONRELATE_SOURCE_<CAP>=<name>`` forces a specific source (manual); "auto" or unset
    lets the registry choose the best available (paid preferred). An unavailable pin falls
    back to auto with a warning rather than crashing the run.
    """
    pin = (os.getenv(f"RECONRELATE_SOURCE_{capability.upper()}") or "").strip()
    if pin and pin.lower() != "auto":
        chosen = reg.get(capability, name=pin)
        if chosen is not None and _allowed(chosen, allow_billable):
            return chosen
        if chosen is not None:
            logger.warning("Configured billable source %r for %s is excluded by the free profile.", pin, capability)
            return None
        logger.warning("Configured source %r for %s is unavailable; using auto.", pin, capability)
    return next((provider for provider in reg.get_all(capability)
                 if _allowed(provider, allow_billable)), None)


def _pick_all(reg: ProviderRegistry, capability: str, *, allow_billable: bool = True) -> list[object]:
    """Resolve an ordered cascade, while a source pin deliberately selects one source."""
    pin = (os.getenv(f"RECONRELATE_SOURCE_{capability.upper()}") or "").strip()
    if pin and pin.lower() != "auto":
        chosen = reg.get(capability, name=pin)
        if chosen is not None and _allowed(chosen, allow_billable):
            return [chosen]
        if chosen is not None:
            logger.warning("Configured billable source %r for %s is excluded by the free profile.", pin, capability)
            return []
        logger.warning("Configured source %r for %s is unavailable; using auto.", pin, capability)
    return [provider for provider in reg.get_all(capability) if _allowed(provider, allow_billable)]


def build_runtime(
    settings: Settings,
    ollama_model: str | None = None,
    fast_model: str | None = None,
    registry: ProviderRegistry | None = None,
) -> Runtime:
    conn = get_connection(settings.db_path)
    init_db(conn)
    restrict_sqlite_file_permissions(settings.db_path)
    repository = GraphRepository(conn)
    model = (ollama_model or settings.llm_model or "qwen2.5:7b-instruct").strip()
    resolved_fast = (fast_model or settings.fast_model or "").strip()
    resolved_models = [model, *([resolved_fast] if resolved_fast else [])]
    cloud_models = [item for item in resolved_models if is_cloud_model(item)]
    resolved_litellm_model = _litellm_model_id(cloud_models[0]) if cloud_models else _litellm_model_id(model)
    if cloud_models and not settings.cloud_approved:
        raise SecurityError(
            "Cloud LLM spending is not approved for this run. Add --approve-cloud and a positive "
            "--max-cloud-tokens ceiling."
        )
    if cloud_models and not settings.llm_allow_cloud:
        raise SecurityError(
            "Cloud LLM calls are disabled (RECONRELATE_LLM_ALLOW_CLOUD=false) but the "
            f"configured model resolves to {resolved_litellm_model!r}. Use an Ollama model "
            "(e.g. LLM_MODEL=qwen2.5:7b-instruct or ollama/...) or set RECONRELATE_LLM_ALLOW_CLOUD=true."
        )
    if cloud_models and settings.max_cloud_tokens <= 0:
        raise SecurityError("Cloud LLM runs require a positive max-cloud-tokens ceiling.")
    if cloud_models and settings.max_cloud_cost_usd <= 0:
        raise SecurityError("Cloud LLM runs require a positive max-cloud-cost-usd ceiling.")
    relationship_engine = RelationshipEngine(
        llm_client=LLMClient(
            model=model,
            fast_model=resolved_fast,
            api_base=settings.ollama_api_base,
            timeout_sec=settings.llm_timeout_sec,
            budget=ModelBudget(
                max_calls=settings.max_model_calls,
                max_input_tokens=settings.max_model_input_tokens,
                max_output_tokens=settings.max_model_output_tokens,
                max_cloud_tokens=settings.max_cloud_tokens,
                max_cloud_cost_microusd=math.ceil(settings.max_cloud_cost_usd * 1_000_000),
            ),
            telemetry_sink=repository.record_model_call,
            durable_budget_sink=repository.reserve_model_budget,
            model_cache_lookup=repository.get_cached_model_result,
        ),
        score_threshold=settings.pivot_score_threshold,
        escalate_only=settings.llm_escalate_only,
        tracker_verification_candidates=settings.max_domains_per_identifier,
    )
    reg = registry or default_registry()
    allow_billable = settings.provider_tier == "byok" and settings.paid_approved
    orchestrator = RunOrchestrator(
        repository=repository,
        whois_provider=_pick_all(reg, "whois", allow_billable=allow_billable),
        basic_info_provider=_pick(reg, "basic_info", allow_billable=allow_billable),
        reverse_whois_provider=_pick(reg, "reverse_whois", allow_billable=allow_billable),
        # Subdomains stay explicit: the orchestrator consumes crt.sh + HackerTarget as a
        # two-source waterfall, so they aren't a single-provider pick.
        crtsh_provider=reg.get("subdomains", name="crtsh"),
        hackertarget_provider=reg.get("subdomains", name="hackertarget"),
        subfinder_provider=reg.get("subdomains", name="subfinder"),
        dns_provider=_pick(reg, "dns", allow_billable=allow_billable),
        relationship_engine=relationship_engine,
        settings=settings,
        acquisitions_provider=_pick_all(reg, "acquisitions", allow_billable=allow_billable),
        historical_web_provider=_pick(reg, "historical_web", allow_billable=allow_billable),
    )
    return Runtime(repository=repository, orchestrator=orchestrator, close=conn.close)
