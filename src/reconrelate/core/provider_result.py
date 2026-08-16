"""Provider-neutral result envelopes and deterministic observation normalization."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from typing import Any, Generic, Literal, TypeVar

from reconrelate.core.evidence import Observation
from reconrelate.core.types import BasicIntelRecord, HistoricalWebRecord, SubdomainFinding, WhoisRecord
from reconrelate.core.tracker import tracker_confidence


T = TypeVar("T")
ProviderStatus = Literal["success", "empty", "error"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def provider_identity(provider: object, fallback: str) -> str:
    """Return the registry identity attached to an adapter, with a stable fallback."""
    return str(getattr(provider, "__reconrelate_provider__", fallback))


def provider_is_billable(provider: object) -> bool:
    return bool(getattr(provider, "__reconrelate_billable__", False))


def provider_concurrency_limit(provider: object, default: int = 4) -> int:
    return max(1, int(getattr(provider, "__reconrelate_concurrency__", default)))


def provider_rate_limit(provider: object, default: int = 60) -> int:
    return max(1, int(getattr(provider, "__reconrelate_rate_per_minute__", default)))


def provider_response_limit(provider: object, default: int = 1_048_576) -> int:
    return max(1, int(getattr(provider, "__reconrelate_max_response_bytes__", default)))


def provider_result_limit(provider: object, default: int = 1_000) -> int:
    return max(1, int(getattr(provider, "__reconrelate_max_result_items__", default)))


def provider_request_limit(provider: object, default: int = 1) -> int:
    return max(1, int(getattr(provider, "__reconrelate_max_requests__", default)))


def provider_page_limit(provider: object, default: int = 1) -> int:
    return max(1, int(getattr(provider, "__reconrelate_max_pages__", default)))


def provider_timeout(provider: object, default: float) -> float:
    return max(0.01, float(getattr(provider, "__reconrelate_timeout_sec__", default)))


def subdomain_values(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        item.domain if isinstance(item, SubdomainFinding) else str(item)
        for item in value
    ]


@dataclass(frozen=True, slots=True)
class ProviderResult(Generic[T]):
    provider: str
    capability: str
    status: ProviderStatus
    collected_at: str
    data: T | None = None
    error_code: str | None = None
    error_message: str | None = None
    subject: str | None = None

    @classmethod
    def from_data(
        cls, provider: str, capability: str, data: T | None, *, subject: str | None = None
    ) -> "ProviderResult[T]":
        empty = data is None or data == []
        return cls(provider, capability, "empty" if empty else "success", _now_iso(), data, subject=subject)

    @classmethod
    def failure(
        cls, provider: str, capability: str, error: Exception, *, code: str = "provider_error",
        subject: str | None = None,
    ) -> "ProviderResult[T]":
        return cls(provider, capability, "error", _now_iso(), None, code, str(error)[:500], subject)


def _raw_hash(value: Any) -> str:
    if is_dataclass(value):
        value = asdict(value)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _observation(
    result: ProviderResult[Any], subject: str, predicate: str, object_type: str,
    object_value: str, confidence: float, *, source_record_id: str | None = None,
) -> Observation:
    return Observation.build(
        subject_type="domain",
        subject_value_norm=subject,
        predicate=predicate,
        object_type=object_type,
        object_value_norm=object_value,
        source=result.provider,
        source_record_id=source_record_id,
        observed_at=result.collected_at,
        confidence=confidence,
        normalized={"capability": result.capability, "value": object_value},
        raw_hash=_raw_hash(result.data),
    )


def observations_from_result(result: ProviderResult[Any]) -> list[Observation]:
    """Convert a successful provider result into immutable, provider-neutral facts."""
    if result.status != "success" or result.data is None:
        return []
    data = result.data
    observations: list[Observation] = []
    if isinstance(data, WhoisRecord):
        source = str(data.raw.get("source", result.provider))
        effective = ProviderResult(source, result.capability, result.status, result.collected_at, data,
                                   subject=result.subject)
        confidence = 0.1 if source in {"fallback", "whois_error", "whois_empty"} else 0.7
        fields = (
            ("registrant_name", "registered_by_name", "person", data.registrant_name),
            ("registrant_org", "registered_by_org", "organization", data.registrant_org),
            ("registrant_email", "has_registrant_email", "email", data.registrant_email),
            ("registrant_phone", "has_registrant_phone", "phone", data.registrant_phone),
            ("creation_date", "created_at", "timestamp", data.creation_date),
            ("expiration_date", "expires_at", "timestamp", data.expiration_date),
        )
        for field_name, predicate, object_type, value in fields:
            if value:
                observations.append(_observation(effective, data.domain, predicate, object_type, value, confidence,
                                                 source_record_id=field_name))
        for nameserver in data.nameservers:
            observations.append(_observation(effective, data.domain, "has_nameserver", "nameserver",
                                             nameserver, confidence))
    elif isinstance(data, BasicIntelRecord):
        if data.title:
            observations.append(_observation(result, data.domain, "has_page_title", "text", data.title, 0.5))
        if data.description:
            observations.append(_observation(result, data.domain, "has_page_description", "text", data.description, 0.4))
        for alias in data.aliases:
            observations.append(_observation(result, data.domain, "uses_alias", "name", alias, 0.4))
        for tracker in data.tracker_ids:
            observations.append(_observation(
                result, data.domain, "uses_tracker", "tracker", tracker,
                tracker_confidence(tracker),
            ))
        if data.copyright_org:
            observations.append(_observation(result, data.domain, "claims_copyright_org", "organization",
                                             data.copyright_org, 0.65))
        if data.redirect_domain:
            observations.append(_observation(
                result, data.domain, "redirects_to_domain", "domain", data.redirect_domain, 0.9,
                source_record_id=data.final_url or None,
            ))
        for entity in data.legal_entities:
            observations.append(_observation(
                result, data.domain, "states_legal_entity", "organization", entity, 0.85,
                source_record_id=data.legal_entity_sources.get(entity) or None,
            ))
    elif result.capability == "dns":
        mappings = (
            ("a_records", "resolves_to", "ip"), ("aaaa_records", "resolves_to", "ip"),
            ("mx_records", "has_mx", "mx"), ("ns_records", "has_nameserver", "nameserver"),
            ("cname_records", "has_cname", "domain"), ("txt_records", "publishes_txt", "text"),
        )
        for attr, predicate, object_type in mappings:
            for value in getattr(data, attr, []):
                observations.append(_observation(result, data.domain, predicate, object_type, value, 0.8))
    elif result.capability == "subdomains" and isinstance(data, list):
        subject = str(result.subject or "")
        for value in data:
            if isinstance(value, SubdomainFinding):
                sources = value.sources or ["unknown"]
                for source in sources:
                    effective = ProviderResult(
                        f"{result.provider}/{source}", result.capability, result.status,
                        result.collected_at, data, subject=result.subject,
                    )
                    observations.append(_observation(
                        effective, subject, "has_subdomain", "domain", value.domain, 0.9,
                        source_record_id=source,
                    ))
            else:
                observations.append(_observation(
                    result, subject, "has_subdomain", "domain", str(value), 0.9
                ))
    elif result.capability == "historical_web" and isinstance(data, list):
        for record in data:
            if not isinstance(record, HistoricalWebRecord):
                continue
            common = {
                "subject_type": "domain",
                "subject_value_norm": record.domain,
                "source": result.provider,
                "source_record_id": record.digest or record.archive_url,
                "observed_at": record.captured_at,
                "valid_from": record.captured_at,
            }
            metadata = {
                "archive_url": record.archive_url,
                "original_url": record.original_url,
                "capture_timestamp": record.captured_at,
                "digest": record.digest,
                "historical": True,
            }
            observations.append(Observation.build(
                **common, predicate="has_archived_snapshot", object_type="url",
                object_value_norm=record.archive_url, confidence=1.0, normalized=metadata,
            ))
            if record.title:
                observations.append(Observation.build(
                    **common, predicate="had_page_title", object_type="text",
                    object_value_norm=record.title, confidence=0.6, normalized=metadata,
                ))
            for tracker in record.tracker_ids:
                observations.append(Observation.build(
                    **common, predicate="historically_used_tracker", object_type="tracker",
                    object_value_norm=tracker,
                    confidence=min(0.85, tracker_confidence(tracker)), normalized=metadata,
                ))
            if record.copyright_org:
                observations.append(Observation.build(
                    **common, predicate="historically_claimed_copyright_org",
                    object_type="organization", object_value_norm=record.copyright_org,
                    confidence=0.6, normalized=metadata,
                ))
    return observations
