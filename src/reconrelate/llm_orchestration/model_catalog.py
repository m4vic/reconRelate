"""Release-versioned model metadata and zero-generation setup diagnostics."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any

from reconrelate.config.settings import Settings
from reconrelate.core.errors import ModelPricingUnavailableError
from reconrelate.llm_orchestration.relationship_engine import (
    MODEL_POLICY_VERSION,
    _litellm_model_id,
    is_cloud_model,
)
from reconrelate.llm_orchestration.response_parser import RELATIONSHIP_RESPONSE_FORMAT
from reconrelate.llm_orchestration.model_pricing import (
    PRICE_CATALOG_VERSION,
    estimate_cloud_cost_microusd,
)

MODEL_CATALOG_VERSION = "2026.08.14-v2"
_MAX_TAG_RESPONSE_BYTES = 1_000_000


@dataclass(frozen=True, slots=True)
class ModelCatalogEntry:
    model: str
    runtime: str
    task: str
    compatibility: str
    quality_status: str
    evidence: str
    input_usd_per_million: str | None = None
    output_usd_per_million: str | None = None


MODEL_CATALOG = (
    ModelCatalogEntry(
        model="ollama/qwen2.5:7b-instruct",
        runtime="local",
        task="relationship_pivot",
        compatibility="verified",
        quality_status="unevaluated",
        evidence="Real local strict-schema call on 2026-08-14; no held-out quality corpus.",
    ),
    ModelCatalogEntry(
        model="gpt-5-mini",
        runtime="cloud",
        task="relationship_pivot",
        compatibility="unverified",
        quality_status="unevaluated",
        evidence="Official price/schema candidate; no paid compatibility or held-out quality run has been authorized.",
        input_usd_per_million="0.25",
        output_usd_per_million="2.00",
    ),
    ModelCatalogEntry(
        model="gpt-5.6-luna",
        runtime="cloud",
        task="relationship_pivot",
        compatibility="unverified",
        quality_status="unevaluated",
        evidence="Official cost-sensitive candidate; no paid compatibility or held-out quality run has been authorized.",
        input_usd_per_million="0.20",
        output_usd_per_million="1.20",
    ),
)


@dataclass(frozen=True, slots=True)
class ModelCheck:
    name: str
    status: str
    detail: str


@dataclass(frozen=True, slots=True)
class ModelDoctorResult:
    catalog_version: str
    configured_model: str
    runtime: str
    ready: bool
    checks: tuple[ModelCheck, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "catalog_version": self.catalog_version,
            "configured_model": self.configured_model,
            "runtime": self.runtime,
            "ready": self.ready,
            "checks": [asdict(check) for check in self.checks],
        }


def catalog_payload() -> dict[str, Any]:
    return {
        "catalog_version": MODEL_CATALOG_VERSION,
        "automatic_recommendation": None,
        "recommendation_reason": "No model has passed the held-out quality and cost gate.",
        "models": [asdict(entry) for entry in MODEL_CATALOG],
    }


def _installed_ollama_models(api_base: str, timeout_sec: float) -> set[str]:
    request = urllib.request.Request(
        f"{api_base.rstrip('/')}/api/tags",
        headers={"Accept": "application/json", "User-Agent": "ReconRelate/model-doctor"},
    )
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        length = response.headers.get("Content-Length")
        if length and int(length) > _MAX_TAG_RESPONSE_BYTES:
            raise ValueError("Ollama tags response exceeds 1 MB")
        raw = response.read(_MAX_TAG_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_TAG_RESPONSE_BYTES:
        raise ValueError("Ollama tags response exceeds 1 MB")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise ValueError("Ollama tags response has an invalid shape")
    return {
        str(item.get("name", "")).strip()
        for item in payload["models"] if isinstance(item, dict) and item.get("name")
    }


def _model_is_installed(configured: str, installed: set[str]) -> bool:
    return configured in installed or (
        ":" not in configured and f"{configured}:latest" in installed
    )


def _cloud_credential(settings: Settings, model: str) -> tuple[str, bool]:
    if model.startswith("anthropic/"):
        name = "ANTHROPIC_API_KEY"
        return name, bool(os.getenv(name))
    if "/" in model:
        name = f"{model.split('/', 1)[0].upper()}_API_KEY"
        return name, bool(os.getenv(name))
    return "OPENAI_API_KEY", bool(settings.openai_api_key)


def diagnose_model(settings: Settings) -> ModelDoctorResult:
    configured = _litellm_model_id(settings.llm_model or "qwen2.5:7b-instruct")
    fast = _litellm_model_id(settings.fast_model) if settings.fast_model else ""
    models = [("primary", configured), *([("fast", fast)] if fast and fast != configured else [])]
    local_models = [(role, model) for role, model in models if not is_cloud_model(model)]
    cloud_models = [(role, model) for role, model in models if is_cloud_model(model)]
    checks: list[ModelCheck] = [
        ModelCheck("model_policy", "ok", MODEL_POLICY_VERSION),
        ModelCheck(
            "model_routing", "ok",
            f"economical-first-v1; fast={fast}" if fast and fast != configured else "single-model-v1",
        ),
        ModelCheck(
            "structured_output", "ok",
            str(RELATIONSHIP_RESPONSE_FORMAT["json_schema"]["name"]),
        ),
    ]
    ready = True
    if cloud_models:
        gate_ok = settings.llm_allow_cloud
        checks.append(ModelCheck(
            "cloud_admin_gate", "ok" if gate_ok else "error",
            "enabled" if gate_ok else "disabled; set allow_cloud true",
        ))
        ready = ready and gate_ok
        for role, model in cloud_models:
            key_name, key_ok = _cloud_credential(settings, model)
            checks.append(ModelCheck(
                "credential" if role == "primary" else f"credential:{role}",
                "ok" if key_ok else "error",
                f"{key_name} is configured" if key_ok else f"{key_name} is missing",
            ))
            ready = ready and key_ok
            try:
                estimate_cloud_cost_microusd(model, 0, 0)
            except ModelPricingUnavailableError as exc:
                price_ok = False
                price_detail = str(exc)
            else:
                price_ok = True
                price_detail = PRICE_CATALOG_VERSION
            checks.append(ModelCheck(
                "price_envelope" if role == "primary" else f"price_envelope:{role}",
                "ok" if price_ok else "error", price_detail,
            ))
            ready = ready and price_ok
        checks.append(ModelCheck(
            "per_run_approval", "required",
            "Every run still requires --approve-cloud and a positive --max-cloud-tokens.",
        ))
    if local_models:
        try:
            installed = _installed_ollama_models(
                settings.ollama_api_base, min(5.0, float(settings.request_timeout_sec))
            )
        except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
            checks.append(ModelCheck(
                "ollama", "error", f"unreachable or invalid response: {exc.__class__.__name__}"
            ))
            ready = False
        else:
            checks.append(ModelCheck(
                "ollama", "ok", f"reachable at {settings.ollama_api_base}"
            ))
            for role, model in local_models:
                local_name = model.removeprefix("ollama/")
                found = _model_is_installed(local_name, installed)
                checks.append(ModelCheck(
                    "model_installed" if role == "primary" else f"model_installed:{role}",
                    "ok" if found else "error",
                    f"{local_name} is installed" if found else f"{local_name} is not installed",
                ))
                ready = ready and found
    coherent = settings.per_domain_timeout_sec > settings.llm_timeout_sec
    checks.append(ModelCheck(
        "timeouts", "ok" if coherent else "error",
        f"model={settings.llm_timeout_sec}s, domain={settings.per_domain_timeout_sec}s",
    ))
    return ModelDoctorResult(
        catalog_version=MODEL_CATALOG_VERSION,
        configured_model=configured,
        runtime=("mixed" if cloud_models and local_models else "cloud" if cloud_models else "local"),
        ready=ready and coherent,
        checks=tuple(checks),
    )


def render_model_doctor(result: ModelDoctorResult) -> str:
    lines = [
        f"Model doctor: {'ready' if result.ready else 'NOT READY'}",
        f"Configured: {result.configured_model} ({result.runtime})",
        f"Catalog: {result.catalog_version}",
    ]
    lines.extend(f"  [{check.status}] {check.name}: {check.detail}" for check in result.checks)
    return "\n".join(lines)
