"""
llm_orchestration/relationship_engine.py

Relationship mapping orchestrator: ties domain evidence to corporate entities.
Coordinates deterministic baseline scoring and budget-gated LLM calls.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Callable

import litellm

try:
    litellm.suppress_debug_info = True
except AttributeError:
    pass

from reconrelate.core.normalize import normalize_identifier
from reconrelate.core.query_optimizer import allocate_pivots
from reconrelate.core.types import BasicIntelRecord, PivotCandidate, WhoisRecord
from reconrelate.llm_orchestration.deterministic_scorer import (
    STRONG_PIVOT_SCORE,
    extract_deterministic_pivots,
    extract_whois_pivot_candidates as _extract_whois_pivot_candidates,
)
from reconrelate.llm_orchestration.prompt_builder import (
    MAX_LLM_CONTEXT_CHARS,
    SYSTEM_PROMPT,
    build_user_message,
    compact_context_for_llm,
)
from reconrelate.llm_orchestration.model_budget import ModelBudget, ModelReservation
from reconrelate.llm_orchestration.model_telemetry import ModelCallTelemetry
from reconrelate.llm_orchestration.model_pricing import PRICE_CATALOG_VERSION
from reconrelate.llm_orchestration.egress_policy import prepare_model_evidence
from reconrelate.core.errors import ModelBudgetExceededError
from reconrelate.llm_orchestration.response_parser import (
    RELATIONSHIP_RESPONSE_FORMAT,
    parse_llm_response,
    validate_pivot,
)

logger = logging.getLogger(__name__)
MODEL_POLICY_VERSION = "relationship-pivot-v2"
MODEL_ROUTING_POLICY_VERSION = "economical-first-v1"
FAST_ROUTE_ACCEPT_SCORE = 0.75
_CLOUD_MODEL_PREFIXES = {
    "openai", "anthropic", "azure", "azure_ai", "bedrock", "vertex_ai", "gemini",
    "groq", "together_ai", "openrouter", "mistral", "cohere", "deepseek", "xai",
    "perplexity", "fireworks_ai", "replicate", "huggingface", "watsonx", "databricks",
}


@dataclass(frozen=True, slots=True)
class ModelAttempt:
    candidates: list[PivotCandidate]
    status: str
    disposition: str | None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _value(container: object, name: str) -> object:
    if isinstance(container, dict):
        return container.get(name)
    return getattr(container, name, None)


def _reported_int(container: object, name: str) -> int | None:
    value = _value(container, name)
    try:
        parsed = int(value)  # type: ignore[arg-type]
        return parsed if parsed >= 0 else None
    except (TypeError, ValueError):
        return None


def _reported_cost(response: object) -> float | None:
    hidden = _value(response, "_hidden_params") or {}
    value = _value(hidden, "response_cost")
    if value is None:
        value = _value(response, "response_cost")
    try:
        parsed = float(value)  # type: ignore[arg-type]
        return parsed if parsed >= 0 and math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def _litellm_model_id(model: str) -> str:
    """Map bare tags (e.g. qwen2.5:7b-instruct) to litellm provider ids."""
    m = model.strip()
    if not m:
        return "ollama/qwen2.5:7b-instruct"
    if m.lower().startswith("ollama/"):
        return m
    if "/" in m and m.split("/", 1)[0].lower() in _CLOUD_MODEL_PREFIXES:
        return m
    lower = m.lower()
    if lower.startswith("gpt-") or lower.startswith("o1") or lower.startswith("o3") or lower.startswith("chatgpt-"):
        return m
    if lower.startswith("claude") or lower.startswith("anthropic."):
        return m if lower.startswith("anthropic/") else f"anthropic/{m}"
    return f"ollama/{m}"


def is_cloud_model(model: str) -> bool:
    return not _litellm_model_id(model).startswith("ollama/")


class LLMClient:
    """One litellm completion per domain (single evidence bundle)."""

    def __init__(
        self,
        model: str = "qwen2.5:7b-instruct",
        fast_model: str = "",
        api_base: str | None = None,
        custom_api_base: str = "",
        timeout_sec: int = 120,
        budget: ModelBudget | None = None,
        telemetry_sink: Callable[[ModelCallTelemetry], None] | None = None,
        durable_budget_sink: Callable[
            [str, str, str, ModelReservation, str | None], ModelReservation
        ] | None = None,
        model_cache_lookup: Callable[[str], str | None] | None = None,
    ) -> None:
        self.model = model
        self.fast_model = fast_model or ""
        self.api_base = (api_base or "http://localhost:11434").rstrip("/")
        # Only for a `provider=custom` model profile's litellm_id (e.g. "openai/<model>" routed
        # at a self-hosted or third-party endpoint) — never applied to ollama/openai/anthropic.
        self.custom_api_base = custom_api_base.rstrip("/") if custom_api_base else ""
        self.timeout_sec = timeout_sec
        self.budget = budget or ModelBudget(50, 200_000, 25_600, 0)
        self.telemetry_sink = telemetry_sink
        self.durable_budget_sink = durable_budget_sink
        self.model_cache_lookup = model_cache_lookup
        self.sdk_calls = 0

    async def call_unified(
        self, domain: str, evidence: dict, run_metadata: dict | None = None
    ) -> list[PivotCandidate]:
        """Run the optional economical-first route, then the primary model when needed."""
        primary = _litellm_model_id(self.model)
        fast = _litellm_model_id(self.fast_model) if self.fast_model else ""
        if fast and fast != primary:
            first = await self._call_model(
                domain, evidence, run_metadata, model=fast, task="relationship_pivot_fast"
            )
            if first.status == "budget_exceeded":
                return []
            if first.status == "success" and first.disposition == "accepted" and any(
                candidate.score >= FAST_ROUTE_ACCEPT_SCORE and validate_pivot(candidate)
                for candidate in first.candidates
            ):
                return first.candidates
            logger.info("Fast model was insufficient for %s; escalating to primary model", domain)
            strong = await self._call_model(
                domain, evidence, run_metadata, model=primary, task="relationship_pivot_strong"
            )
            return strong.candidates if strong.status == "success" else []
        attempt = await self._call_model(
            domain, evidence, run_metadata, model=primary, task="relationship_pivot"
        )
        return attempt.candidates

    async def _call_model(
        self, domain: str, evidence: dict, run_metadata: dict | None, *, model: str, task: str
    ) -> ModelAttempt:
        litellm_model = _litellm_model_id(model)
        cloud = not litellm_model.startswith("ollama/")
        model_evidence = prepare_model_evidence(evidence, cloud=cloud)
        egress_policy_version = str(model_evidence["_egress_policy"])
        user_message = build_user_message(domain, model_evidence)
        char_count = len(user_message)
        logger.info(
            "LLM relationship call for %s (%d chars) via %s",
            domain,
            char_count,
            litellm_model,
        )

        kwargs: dict = {
            "model": litellm_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0.1,
            "max_tokens": 512,
            "timeout": float(self.timeout_sec),
            "response_format": RELATIONSHIP_RESPONSE_FORMAT,
        }
        if litellm_model.startswith("ollama/"):
            kwargs["api_base"] = self.api_base
            kwargs["num_predict"] = 512
            # Disable Ollama's reasoning mode. Qwen3-family models think by default and spend
            # the entire token budget in the `thinking` field, returning EMPTY content - so a
            # perfectly capable model looks like it always emits invalid output. Measured on
            # qwen3.5:9b: think=True gave 3815 chars of thinking and 0 chars of content;
            # think=False gave valid schema-conforming JSON. This task is structured extraction,
            # not reasoning, so thinking buys nothing even where it does not break the output.
            # Verified to be a no-op for non-thinking models (qwen2.5, llama3.1).
            kwargs["think"] = False
        elif self.custom_api_base:
            kwargs["api_base"] = self.custom_api_base

        started_at = _now_iso()
        started = time.perf_counter()
        reservation = None
        response = None
        status = "error"
        error: Exception | None = None
        result: list[PivotCandidate] = []
        output_disposition: str | None = None
        run_id = str(run_metadata.get("run_id")) if run_metadata and run_metadata.get("run_id") else None
        input_text = SYSTEM_PROMPT + "\n" + user_message
        request_key = hashlib.sha256(
            "\0".join((run_id or "standalone", domain, task,
                        MODEL_POLICY_VERSION, litellm_model, input_text)).encode("utf-8")
        ).hexdigest()
        if run_id and self.model_cache_lookup is not None:
            cached = self.model_cache_lookup(request_key)
            if cached is not None:
                try:
                    values = json.loads(cached)
                    replayed = [
                        PivotCandidate(
                            id_type=str(item["id_type"]), value=str(item["value"]),
                            score=float(item["score"]), reason=str(item["reason"]),
                        )
                        for item in values if isinstance(item, dict)
                    ]
                    logger.info("Replayed normalized model result for %s without SDK call", domain)
                    return ModelAttempt(
                        replayed, "success", "accepted" if replayed else "abstained"
                    )
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    logger.warning("Ignoring malformed cached model result for %s", domain)
        try:
            if run_id and self.durable_budget_sink is not None:
                requested = ModelBudget.estimate(
                    input_text=input_text,
                    output_tokens=512,
                    cloud=cloud,
                    model=litellm_model,
                )
                reservation = self.durable_budget_sink(
                    run_id, litellm_model, domain, requested, request_key
                )
            else:
                reservation = self.budget.reserve(
                    input_text=input_text,
                    output_tokens=512,
                    cloud=cloud,
                    model=litellm_model,
                )
            self.sdk_calls += 1
            response = await asyncio.to_thread(litellm.completion, **kwargs)
            choice = response.choices[0]
            raw_content = (choice.message.content or "").strip()
            parsed = parse_llm_response(raw_content, "relationship")
            result = parsed.pivots
            output_disposition = parsed.disposition
            status = "success"
        except ModelBudgetExceededError as exc:
            error = exc
            status = "budget_exceeded"
            logger.info("LLM budget rejected %s: %s", domain, exc)
        except Exception as exc:
            error = exc
            # The LLM is optional (deterministic scoring is the baseline); a failed or missing
            # model degrades quietly rather than spamming the terminal. Use --verbose to see it.
            logger.info("LLM call failed for %s: %s", domain, exc)
        finally:
            if self.telemetry_sink is not None:
                usage = _value(response, "usage") if response is not None else None
                telemetry = ModelCallTelemetry(
                    run_id=run_id,
                    domain=domain,
                    model=litellm_model,
                    task=task,
                    policy_version=MODEL_POLICY_VERSION,
                    cloud=cloud,
                    status=status,
                    reserved_input_tokens=reservation.input_tokens if reservation else 0,
                    reserved_output_tokens=reservation.output_tokens if reservation else 0,
                    reserved_cloud_tokens=reservation.cloud_tokens if reservation else 0,
                    actual_input_tokens=_reported_int(usage, "prompt_tokens") if usage else None,
                    actual_output_tokens=_reported_int(usage, "completion_tokens") if usage else None,
                    actual_total_tokens=_reported_int(usage, "total_tokens") if usage else None,
                    provider_reported_cost_usd=_reported_cost(response) if response is not None else None,
                    latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
                    error_class=error.__class__.__name__ if error else None,
                    error_message=str(error) if error else None,
                    started_at=started_at,
                    completed_at=_now_iso(),
                    request_key=request_key,
                    result_json=(
                        json.dumps([
                            {"id_type": item.id_type, "value": item.value, "score": item.score,
                             "reason": item.reason}
                            for item in result[:20]
                        ], sort_keys=True, separators=(",", ":"))
                        if status == "success" and output_disposition in {"accepted", "abstained"}
                        else None
                    ),
                    egress_policy_version=egress_policy_version,
                    output_disposition=output_disposition,
                    reserved_cloud_cost_microusd=(
                        reservation.cloud_cost_microusd if reservation else 0
                    ),
                    price_catalog_version=PRICE_CATALOG_VERSION if cloud else "local",
                )
                try:
                    self.telemetry_sink(telemetry)
                except Exception:
                    logger.exception("failed to persist model-call telemetry")
        return ModelAttempt(result, status, output_disposition)


class RelationshipEngine:
    """
    Per-domain pivot extraction orchestrator:
      1. Fast deterministic baseline (WHOIS regex + HTML relationship signals)
      2. Budget gate — escalate to LLM only if baseline is weak
      3. Filter, deduplicate, score, and rank
    """

    def __init__(
        self,
        llm_client: LLMClient,
        score_threshold: float = 0.40,
        escalate_only: bool = True,
        tracker_verification_candidates: int = 3,
    ) -> None:
        self.llm_client = llm_client
        self.score_threshold = score_threshold
        self.escalate_only = escalate_only
        self.tracker_verification_candidates = tracker_verification_candidates

    @property
    def sdk_calls(self) -> int:
        return int(getattr(self.llm_client, "sdk_calls", 0))

    async def select_pivots(
        self,
        domain: str,
        whois: WhoisRecord,
        basic_intel: BasicIntelRecord,
        top_k: int,
        subdomains: list[str] | None = None,
        run_metadata: dict | None = None,
    ) -> list[PivotCandidate]:
        # Step 1: fast deterministic baseline
        candidates = extract_deterministic_pivots(whois=whois, basic_intel=basic_intel, domain=domain)

        if run_metadata:
            logger.debug("Pivot pass metadata (orchestrator / DB only, not sent to LLM): %s", run_metadata)

        # Step 2: budget gate — only escalate to the LLM when the deterministic baseline is weak.
        has_strong = any(c.score >= STRONG_PIVOT_SCORE for c in candidates)
        if self.escalate_only and has_strong:
            logger.info("Relationship mapping for %s: strong deterministic pivot — skipping LLM call", domain)
            return self._finalize(candidates, domain, top_k)

        # Otherwise: one LLM call with domain evidence context
        evidence_for_llm = {
            "domain": domain,
            "whois": {
                "registrant_name": whois.registrant_name,
                "registrant_org": whois.registrant_org,
                "registrant_email": whois.registrant_email,
                "registrant_phone": whois.registrant_phone,
                "nameservers": whois.nameservers,
                "creation_date": str(whois.creation_date),
                "expiration_date": str(whois.expiration_date),
            },
            "basic_intel": {
                "title": basic_intel.title,
                "description": basic_intel.description,
                "aliases": basic_intel.aliases,
                "copyright_org": basic_intel.copyright_org,
                "tracker_ids": basic_intel.tracker_ids,
                "redirect_domain": basic_intel.redirect_domain,
                "legal_entities": basic_intel.legal_entities,
            },
            "subdomains": subdomains or [],
        }
        compact = compact_context_for_llm(evidence_for_llm, MAX_LLM_CONTEXT_CHARS)
        llm_candidates = await self.llm_client.call_unified(
            domain, compact, run_metadata=run_metadata
        )
        candidates.extend(llm_candidates)
        return self._finalize(candidates, domain, top_k)

    def _finalize(self, candidates: list[PivotCandidate], domain: str, top_k: int) -> list[PivotCandidate]:
        """Filter, deduplicate, rank — shared by the gated and LLM paths."""
        filtered: list[PivotCandidate] = []
        seen: set[tuple[str, str]] = set()
        for candidate in candidates:
            if not validate_pivot(candidate):
                continue
            if candidate.score < self.score_threshold:
                continue
            try:
                normalized = normalize_identifier(candidate.id_type, candidate.value)
            except Exception:
                continue
            key = (candidate.id_type, normalized)
            if key in seen:
                continue
            seen.add(key)
            filtered.append(PivotCandidate(
                id_type=candidate.id_type,
                value=normalized,
                score=candidate.score,
                reason=candidate.reason,
            ))

        allocated = allocate_pivots(
            filtered,
            top_k,
            tracker_verification_candidates=self.tracker_verification_candidates,
        )
        logger.info(
            "Relationship mapping for %s: %d total candidates → %d after filter (top %d)",
            domain, len(candidates), len(allocated), top_k
        )
        return [decision.pivot for decision in allocated]
