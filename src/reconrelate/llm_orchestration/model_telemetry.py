"""Typed model-call telemetry persisted independently of model output."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelCallTelemetry:
    run_id: str | None
    domain: str
    model: str
    task: str
    policy_version: str
    cloud: bool
    status: str
    reserved_input_tokens: int
    reserved_output_tokens: int
    reserved_cloud_tokens: int
    actual_input_tokens: int | None
    actual_output_tokens: int | None
    actual_total_tokens: int | None
    provider_reported_cost_usd: float | None
    latency_ms: int
    error_class: str | None
    error_message: str | None
    started_at: str
    completed_at: str
    request_key: str = ""
    result_json: str | None = None
    egress_policy_version: str = "legacy"
    output_disposition: str | None = None
    reserved_cloud_cost_microusd: int = 0
    price_catalog_version: str = "legacy"
