"""Authoritative RDAP domain registration through the IANA bootstrap registry."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

import aiohttp

from reconrelate.core.errors import ProviderAuthError, ProviderMalformedError, ProviderRateLimitError
from reconrelate.core.http import read_limited_json
from reconrelate.core.normalize import normalize_domain
from reconrelate.core.provider_budget import consume_page
from reconrelate.core.types import WhoisRecord
from reconrelate.security.safe_http import safe_client_session, safe_get
from reconrelate.security.safe_target import validate_scan_target

_BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"
_BOOTSTRAP_TTL_SEC = 86_400
_UA = "ReconRelate/0.1 (open-source OSINT relationship mapper)"
_PRIVACY_MARKERS = (
    "redacted for privacy",
    "data protected",
    "whois privacy",
    "domains by proxy",
    "privacy service",
    "contact privacy",
    "identity protection",
)


def _clean_identity(value: Any) -> str:
    if isinstance(value, list):
        value = " ".join(str(item) for item in value if str(item).strip())
    text = " ".join(str(value or "").split()).strip()
    if text.lower().startswith("mailto:"):
        text = text[7:]
    elif text.lower().startswith("tel:"):
        text = text[4:]
    lowered = text.lower()
    if not text or any(marker in lowered for marker in _PRIVACY_MARKERS):
        return ""
    return text[:500]


def _vcard_fields(entity: dict[str, Any]) -> dict[str, str]:
    card = entity.get("vcardArray") or []
    properties = card[1] if isinstance(card, list) and len(card) > 1 else []
    fields = {"name": "", "org": "", "email": "", "phone": ""}
    mapping = {"fn": "name", "org": "org", "email": "email", "tel": "phone"}
    for prop in properties if isinstance(properties, list) else []:
        if not isinstance(prop, list) or len(prop) < 4:
            continue
        field = mapping.get(str(prop[0]).lower())
        if field and not fields[field]:
            fields[field] = _clean_identity(prop[3])
    return fields


def _is_identity_sufficient(record: WhoisRecord) -> bool:
    return any((
        record.registrant_org,
        record.registrant_name,
        record.registrant_email,
        record.registrant_phone,
    ))


class RdapProvider:
    """Resolve a domain's authoritative RDAP service and normalize public registration data."""

    def __init__(self) -> None:
        self._bootstrap: dict[str, tuple[str, ...]] = {}
        self._bootstrap_loaded_at = 0.0
        self._bootstrap_lock = asyncio.Lock()

    async def _get_json(self, url: str, *, max_bytes: int) -> dict[str, Any] | None:
        timeout = aiohttp.ClientTimeout(total=15)
        async with safe_client_session(timeout) as session:
            async with safe_get(
                session,
                url,
                headers={"User-Agent": _UA, "Accept": "application/rdap+json, application/json"},
            ) as response:
                if response.status == 404:
                    return None
                if response.status in {401, 403}:
                    raise ProviderAuthError(f"RDAP server rejected request with HTTP {response.status}")
                if response.status == 429:
                    raise ProviderRateLimitError("RDAP server rate limited the request")
                response.raise_for_status()
                consume_page()
                data = await read_limited_json(response, max_bytes=max_bytes)
        if not isinstance(data, dict):
            raise ProviderMalformedError("RDAP response must be a JSON object")
        return data

    async def _services(self) -> dict[str, tuple[str, ...]]:
        if self._bootstrap and time.monotonic() - self._bootstrap_loaded_at < _BOOTSTRAP_TTL_SEC:
            return self._bootstrap
        async with self._bootstrap_lock:
            if self._bootstrap and time.monotonic() - self._bootstrap_loaded_at < _BOOTSTRAP_TTL_SEC:
                return self._bootstrap
            data = await self._get_json(_BOOTSTRAP_URL, max_bytes=1_048_576)
            services: dict[str, tuple[str, ...]] = {}
            for entry in (data or {}).get("services") or []:
                if not isinstance(entry, list) or len(entry) != 2:
                    raise ProviderMalformedError("IANA RDAP bootstrap contains an invalid service")
                labels, bases = entry
                secure_bases = tuple(
                    str(base) for base in bases
                    if isinstance(base, str) and urlsplit(base).scheme.lower() == "https"
                )
                for label in labels if isinstance(labels, list) else []:
                    normalized = str(label).lower().strip().rstrip(".")
                    if normalized and secure_bases:
                        services[normalized] = secure_bases
            if not services:
                raise ProviderMalformedError("IANA RDAP bootstrap contains no HTTPS services")
            self._bootstrap = services
            self._bootstrap_loaded_at = time.monotonic()
            return services

    async def _base_urls(self, domain: str) -> tuple[str, ...]:
        services = await self._services()
        labels = domain.split(".")
        for offset in range(len(labels)):
            suffix = ".".join(labels[offset:])
            if suffix in services:
                return services[suffix][:3]
        return ()

    @staticmethod
    def _normalize(domain: str, data: dict[str, Any], endpoint: str) -> WhoisRecord:
        if data.get("objectClassName") != "domain":
            raise ProviderMalformedError("RDAP lookup response is not a domain object")
        nameservers: set[str] = set()
        for item in data.get("nameservers") or []:
            if not isinstance(item, dict):
                continue
            raw = str(item.get("ldhName") or "").lower().rstrip(".")
            try:
                if raw:
                    nameservers.add(normalize_domain(raw))
            except Exception:
                continue

        identity = {"name": "", "org": "", "email": "", "phone": ""}
        for entity in data.get("entities") or []:
            if not isinstance(entity, dict):
                continue
            roles = {str(role).lower() for role in entity.get("roles") or []}
            if "registrant" not in roles:
                continue
            fields = _vcard_fields(entity)
            for key, value in fields.items():
                if value and not identity[key]:
                    identity[key] = value

        creation = ""
        expiration = ""
        for event in data.get("events") or []:
            if not isinstance(event, dict):
                continue
            action = str(event.get("eventAction") or "").lower()
            value = str(event.get("eventDate") or "")[:100]
            if action == "registration" and not creation:
                creation = value
            elif action == "expiration" and not expiration:
                expiration = value

        raw_status = data.get("status") or []
        statuses = raw_status if isinstance(raw_status, list) else [raw_status]
        return WhoisRecord(
            domain=domain,
            registrant_name=identity["name"],
            registrant_org=identity["org"],
            registrant_email=identity["email"],
            registrant_phone=identity["phone"],
            nameservers=sorted(nameservers),
            creation_date=creation,
            expiration_date=expiration,
            raw={
                "source": "rdap-iana",
                "endpoint_host": urlsplit(endpoint).hostname or "",
                "handle": str(data.get("handle") or "")[:200],
                "status": [str(value)[:100] for value in statuses[:20]],
                "identity_redacted": not any(identity.values()),
            },
        )

    @staticmethod
    def _related_domain_url(data: dict[str, Any]) -> str:
        for link in data.get("links") or []:
            if not isinstance(link, dict) or str(link.get("rel") or "").lower() != "related":
                continue
            href = str(link.get("href") or "")
            if href and "/domain/" in urlsplit(href).path.lower():
                return href
        return ""

    @staticmethod
    def _merge(primary: WhoisRecord, secondary: WhoisRecord) -> WhoisRecord:
        return WhoisRecord(
            domain=primary.domain,
            registrant_name=primary.registrant_name or secondary.registrant_name,
            registrant_org=primary.registrant_org or secondary.registrant_org,
            registrant_email=primary.registrant_email or secondary.registrant_email,
            registrant_phone=primary.registrant_phone or secondary.registrant_phone,
            nameservers=sorted(set(primary.nameservers) | set(secondary.nameservers)),
            creation_date=primary.creation_date or secondary.creation_date,
            expiration_date=primary.expiration_date or secondary.expiration_date,
            raw={**primary.raw, "related_endpoint_host": secondary.raw.get("endpoint_host", "")},
        )

    async def lookup(self, domain: str) -> WhoisRecord:
        domain = normalize_domain(domain)
        validate_scan_target(domain)
        bases = await self._base_urls(domain)
        if not bases:
            return WhoisRecord(domain=domain, raw={"source": "rdap-iana", "available": False})
        data: dict[str, Any] | None = None
        query_url = ""
        errors: list[Exception] = []
        for base in bases:
            candidate = urljoin(
                base if base.endswith("/") else base + "/", "domain/" + quote(domain)
            )
            try:
                candidate_data = await self._get_json(candidate, max_bytes=2_097_152)
            except Exception as exc:
                errors.append(exc)
                continue
            query_url = candidate
            if candidate_data is not None:
                data = candidate_data
                break
        if data is None and errors and len(errors) == len(bases):
            raise errors[-1]
        if data is None:
            return WhoisRecord(domain=domain, raw={"source": "rdap-iana", "not_found": True})
        record = self._normalize(domain, data, query_url)
        if not _is_identity_sufficient(record):
            related = self._related_domain_url(data)
            if related:
                related_data = await self._get_json(related, max_bytes=2_097_152)
                if related_data is not None:
                    record = self._merge(record, self._normalize(domain, related_data, related))
        return record


def registration_identity_sufficient(record: WhoisRecord) -> bool:
    return _is_identity_sufficient(record)
