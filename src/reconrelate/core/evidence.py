"""Typed evidence and derived-claim records used by the production data model."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


ConfidenceClass = Literal["verified", "probable", "candidate", "rejected"]
EvidencePolarity = Literal["supports", "contradicts"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _stable_hash(parts: list[str]) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Observation:
    subject_type: str
    subject_value_norm: str
    predicate: str
    source: str
    observed_at: str
    dedup_key: str
    object_type: str | None = None
    object_value_norm: str | None = None
    source_record_id: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    confidence: float = 0.0
    normalized: dict[str, Any] = field(default_factory=dict)
    raw_hash: str | None = None
    data_policy_version: str = "provider-data-use-v1"
    cache_allowed: bool = True
    export_scope: str = "normalized"
    raw_retention: str = "hash_only"

    @classmethod
    def build(
        cls,
        *,
        subject_type: str,
        subject_value_norm: str,
        predicate: str,
        source: str,
        object_type: str | None = None,
        object_value_norm: str | None = None,
        source_record_id: str | None = None,
        observed_at: str | None = None,
        valid_from: str | None = None,
        valid_to: str | None = None,
        confidence: float = 0.0,
        normalized: dict[str, Any] | None = None,
        raw_hash: str | None = None,
        data_policy_version: str = "provider-data-use-v1",
        cache_allowed: bool = True,
        export_scope: str = "normalized",
        raw_retention: str = "hash_only",
        idempotency_key: str | None = None,
    ) -> "Observation":
        timestamp = observed_at or utc_now_iso()
        normalized_value = normalized or {}
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("observation confidence must be between 0 and 1")
        required = [subject_type, subject_value_norm, predicate, source]
        if any(not str(value).strip() for value in required):
            raise ValueError("observation subject, predicate, and source must be non-empty")
        if export_scope not in {"none", "derived_only", "normalized"}:
            raise ValueError("invalid observation export scope")
        if raw_retention not in {"none", "hash_only"}:
            raise ValueError("invalid observation raw retention policy")
        if not data_policy_version.strip():
            raise ValueError("observation data policy version is required")
        dedup_key = idempotency_key or _stable_hash([
            source,
            source_record_id or "",
            subject_type,
            subject_value_norm,
            predicate,
            object_type or "",
            object_value_norm or "",
            raw_hash or _canonical_json(normalized_value),
            timestamp,
        ])
        return cls(
            subject_type=subject_type,
            subject_value_norm=subject_value_norm,
            predicate=predicate,
            source=source,
            observed_at=timestamp,
            dedup_key=dedup_key,
            object_type=object_type,
            object_value_norm=object_value_norm,
            source_record_id=source_record_id,
            valid_from=valid_from,
            valid_to=valid_to,
            confidence=confidence,
            normalized=normalized_value,
            raw_hash=raw_hash,
            data_policy_version=data_policy_version,
            cache_allowed=bool(cache_allowed),
            export_scope=export_scope,
            raw_retention=raw_retention,
        )

    def normalized_json(self) -> str:
        return _canonical_json(self.normalized)

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_type": self.subject_type,
            "subject_value_norm": self.subject_value_norm,
            "predicate": self.predicate,
            "source": self.source,
            "observed_at": self.observed_at,
            "dedup_key": self.dedup_key,
            "object_type": self.object_type,
            "object_value_norm": self.object_value_norm,
            "source_record_id": self.source_record_id,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "confidence": self.confidence,
            "normalized": self.normalized,
            "raw_hash": self.raw_hash,
            "data_policy_version": self.data_policy_version,
            "cache_allowed": self.cache_allowed,
            "export_scope": self.export_scope,
            "raw_retention": self.raw_retention,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Observation":
        return cls(
            subject_type=str(value["subject_type"]),
            subject_value_norm=str(value["subject_value_norm"]),
            predicate=str(value["predicate"]),
            source=str(value["source"]),
            observed_at=str(value["observed_at"]),
            dedup_key=str(value["dedup_key"]),
            object_type=str(value["object_type"]) if value.get("object_type") is not None else None,
            object_value_norm=(
                str(value["object_value_norm"]) if value.get("object_value_norm") is not None else None
            ),
            source_record_id=(
                str(value["source_record_id"]) if value.get("source_record_id") is not None else None
            ),
            valid_from=str(value["valid_from"]) if value.get("valid_from") is not None else None,
            valid_to=str(value["valid_to"]) if value.get("valid_to") is not None else None,
            confidence=float(value.get("confidence", 0.0)),
            normalized=dict(value.get("normalized") or {}),
            raw_hash=str(value["raw_hash"]) if value.get("raw_hash") is not None else None,
            data_policy_version=str(value.get("data_policy_version") or "legacy"),
            cache_allowed=bool(value.get("cache_allowed", True)),
            export_scope=str(value.get("export_scope") or "normalized"),
            raw_retention=str(value.get("raw_retention") or "hash_only"),
        )


@dataclass(frozen=True, slots=True)
class Claim:
    claim_type: str
    subject_type: str
    subject_value_norm: str
    object_type: str
    object_value_norm: str
    status: str
    confidence_class: ConfidenceClass
    score: float
    policy_version: str
    claim_key: str
    valid_from: str | None = None
    valid_to: str | None = None

    @classmethod
    def build(
        cls,
        *,
        claim_type: str,
        subject_type: str,
        subject_value_norm: str,
        object_type: str,
        object_value_norm: str,
        status: str,
        confidence_class: ConfidenceClass,
        score: float,
        policy_version: str,
        valid_from: str | None = None,
        valid_to: str | None = None,
    ) -> "Claim":
        if confidence_class not in {"verified", "probable", "candidate", "rejected"}:
            raise ValueError("invalid claim confidence class")
        if not 0.0 <= score <= 1.0:
            raise ValueError("claim score must be between 0 and 1")
        required = [
            claim_type, subject_type, subject_value_norm, object_type,
            object_value_norm, status, policy_version,
        ]
        if any(not str(value).strip() for value in required):
            raise ValueError("claim fields must be non-empty")
        claim_key = _stable_hash([
            claim_type,
            subject_type,
            subject_value_norm,
            object_type,
            object_value_norm,
            status,
            policy_version,
            valid_from or "",
            valid_to or "",
        ])
        return cls(
            claim_type=claim_type,
            subject_type=subject_type,
            subject_value_norm=subject_value_norm,
            object_type=object_type,
            object_value_norm=object_value_norm,
            status=status,
            confidence_class=confidence_class,
            score=score,
            policy_version=policy_version,
            claim_key=claim_key,
            valid_from=valid_from,
            valid_to=valid_to,
        )
