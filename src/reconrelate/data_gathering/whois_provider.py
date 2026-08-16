from __future__ import annotations

from collections.abc import Iterable
from reconrelate.core.types import WhoisRecord
from reconrelate.core.errors import ProviderError
from reconrelate.core.provider_budget import consume_page, consume_request
from reconrelate.core.sdk_process import run_sdk_operation
from reconrelate.security.safe_target import validate_scan_target


def _first_string(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            if isinstance(item, str) and item.strip():
                return item.strip()
    return ""


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, dict)):
        return "\n".join(str(item) for item in value if str(item).strip())
    return str(value)


class WhoisProvider:
    """WHOIS provider backed by python-whois in a killable worker process."""

    async def lookup(self, domain: str) -> WhoisRecord:
        validate_scan_target(domain)
        consume_request()
        try:
            result = await run_sdk_operation(
                "whois", {"domain": domain}, timeout_sec=30, max_output_bytes=524_288
            )
        except ProviderError:
            raise
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("WHOIS lookup failed for %s: %s", domain, e)
            raise ProviderError(f"WHOIS lookup failed for {domain}: {e}") from e

        if not result.get("available"):
            return WhoisRecord(domain=domain, raw={"source": "python-whois", "available": False})
        consume_page()
        data = result.get("record") or {}
        if not isinstance(data, dict):
            raise ProviderError("WHOIS worker record must be an object")

        if not data:
            return WhoisRecord(domain=domain, raw={"source": "whois_empty"})

        name_servers = data.get("name_servers") or data.get("nserver") or []
        if isinstance(name_servers, str):
            name_servers = [name_servers]
        normalized_ns = sorted({str(ns).strip().lower().rstrip(".") for ns in name_servers if str(ns).strip()})

        raw_text = "\n".join(
            part
            for part in (
                _as_text(data.get("text")),
                _as_text(data.get("raw")),
                _as_text(data),
            )
            if part.strip()
        )
        return WhoisRecord(
            domain=domain,
            registrant_name=_first_string(data.get("name")),
            registrant_org=_first_string(data.get("org")),
            registrant_email=_first_string(data.get("emails") or data.get("email")),
            registrant_phone=_first_string(data.get("phone")),
            nameservers=normalized_ns,
            creation_date=_first_string(data.get("creation_date")),
            expiration_date=_first_string(data.get("expiration_date")),
            raw={"source": "python-whois", "text": raw_text},
        )
