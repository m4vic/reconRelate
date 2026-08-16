"""
DNS record resolver. Queries A, AAAA, MX, CNAME, TXT, NS records
for a domain using the system's DNS resolver via the socket + standard library.
No external dependencies needed (no dnspython required).

Async interface: all blocking I/O runs in a bounded worker process.
"""
from __future__ import annotations

import logging
import socket
from dataclasses import dataclass, field

from reconrelate.security.safe_target import validate_scan_target
from reconrelate.core.provider_budget import consume_page, consume_request
from reconrelate.core.sdk_process import run_sdk_operation

logger = logging.getLogger(__name__)


@dataclass
class DNSResult:
    domain: str
    a_records: list[str] = field(default_factory=list)       # IPv4 addresses
    aaaa_records: list[str] = field(default_factory=list)     # IPv6 addresses
    mx_records: list[str] = field(default_factory=list)       # Mail servers
    ns_records: list[str] = field(default_factory=list)       # Nameservers
    cname_records: list[str] = field(default_factory=list)    # Aliases
    txt_records: list[str] = field(default_factory=list)      # SPF, DKIM, etc.


class DNSProvider:
    """
    Resolves DNS records using the standard library.
    Works everywhere, no pip install needed.
    For deeper DNS (MX, TXT, NS), we try dnspython if available,
    otherwise fall back to socket-only (A/AAAA records).
    """

    def __init__(self) -> None:
        # Try importing dnspython — it's optional
        try:
            import dns.resolver
            self._resolver = dns.resolver.Resolver()
            self._resolver.timeout = 5
            self._resolver.lifetime = 10
            self._has_dnspython = True
        except ImportError:
            self._has_dnspython = False
            logger.debug("dnspython not installed; DNS lookups limited to A/AAAA only")

    def _lookup_sync(self, domain: str) -> DNSResult:
        """Blocking DNS resolution used only inside the isolated SDK worker."""
        validate_scan_target(domain)
        result = DNSResult(domain=domain)

        # A records (always available via socket)
        try:
            consume_request()
            infos = socket.getaddrinfo(domain, None, socket.AF_INET)
            consume_page()
            result.a_records = sorted({info[4][0] for info in infos})
        except socket.gaierror:
            pass

        # AAAA records
        try:
            consume_request()
            infos = socket.getaddrinfo(domain, None, socket.AF_INET6)
            consume_page()
            result.aaaa_records = sorted({info[4][0] for info in infos})
        except socket.gaierror:
            pass

        if self._has_dnspython:
            result.mx_records = self._query_dns(domain, "MX")
            result.ns_records = self._query_dns(domain, "NS")
            result.txt_records = self._query_dns(domain, "TXT")
            result.cname_records = self._query_dns(domain, "CNAME")

        logger.info(
            "DNS for %s: %d A, %d AAAA, %d MX, %d NS, %d TXT",
            domain,
            len(result.a_records),
            len(result.aaaa_records),
            len(result.mx_records),
            len(result.ns_records),
            len(result.txt_records),
        )
        return result

    async def lookup(self, domain: str) -> DNSResult:
        """Resolve DNS in a killable worker so cancellation cannot leak blocking sockets."""
        validate_scan_target(domain)
        max_requests = max(1, int(getattr(self, "__reconrelate_max_requests__", 6)))
        max_pages = max(1, int(getattr(self, "__reconrelate_max_pages__", 6)))
        result = await run_sdk_operation(
            "dns",
            {"domain": domain, "max_requests": max_requests, "max_pages": max_pages},
            timeout_sec=30,
            max_output_bytes=1_048_576,
        )
        for _ in range(int(result.get("requests") or 0)):
            consume_request()
        for _ in range(int(result.get("pages") or 0)):
            consume_page()
        record = result.get("record") or {}
        if not isinstance(record, dict):
            raise ValueError("DNS worker record must be an object")
        allowed = set(DNSResult.__dataclass_fields__)
        return DNSResult(**{key: value for key, value in record.items() if key in allowed})

    def _query_dns(self, domain: str, rdtype: str) -> list[str]:
        try:
            import dns.resolver
            consume_request()
            answers = self._resolver.resolve(domain, rdtype)
            consume_page()
            return sorted(str(rdata).rstrip(".") for rdata in answers)
        except Exception:
            return []
