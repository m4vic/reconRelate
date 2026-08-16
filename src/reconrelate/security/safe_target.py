"""
Outbound target guardrails used by the resolver-enforced safe HTTP transport.

ReconRelate builds URLs from user-supplied hostnames. This module blocks explicit internal,
loopback, link-local, and cloud-metadata targets and validates resolved IPs. The safe HTTP layer
uses these checks inside aiohttp's resolver and at every redirect hop.
"""
from __future__ import annotations

import ipaddress
import re

from reconrelate.core.errors import SecurityError

# Hostnames that must never be used as scan targets for client-side HTTP fetches.
_BLOCKED_HOSTNAMES: frozenset[str] = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "::1",
        "0.0.0.0",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data.ec2.internal",
        "169.254.169.254",  # often used as hostname in SSRF payloads
    }
)

# Suspicious TLD / suffix patterns (common internal / mDNS).
_BLOCKED_SUFFIXES: tuple[str, ...] = (
    ".local",
    ".localhost",
    ".internal",
    ".lan",
    ".home",
    ".localdomain",
)


def _is_blocked_suffix(host: str) -> bool:
    h = host.lower().rstrip(".")
    return any(h.endswith(s) for s in _BLOCKED_SUFFIXES)


def _ip_from_host(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """If host is a literal IP string, return it; else None."""
    host = host.strip().lower().rstrip(".")
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _unsafe_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    doc = getattr(addr, "is_documentation", None)
    is_doc = bool(doc()) if callable(doc) else False
    site = getattr(addr, "is_site_local", None)
    is_site = bool(site()) if callable(site) else False
    return bool(
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or is_doc
        or is_site
    )


def validate_resolved_ip(address: str) -> None:
    """Reject a DNS answer that is not globally routable public address space."""
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError as exc:
        raise SecurityError(f"DNS resolver returned an invalid address: {address!r}") from exc
    if _unsafe_ip(parsed):
        raise SecurityError(f"DNS resolution blocked (non-public IP): {address!r}")


def validate_scan_target(host: str) -> None:
    """
    Ensure `host` is a normalized public scan target (apex/FQDN).

    Raises:
        SecurityError: if the target is blocked as internal/metadata/special.
    """
    if not host or not host.strip():
        raise SecurityError("empty scan target")

    h = host.strip().lower().rstrip(".")

    if h in _BLOCKED_HOSTNAMES:
        raise SecurityError(f"scan target blocked (hostname denylist): {host!r}")

    if _is_blocked_suffix(h):
        raise SecurityError(f"scan target blocked (internal-style suffix): {host!r}")

    literal = _ip_from_host(h)
    if literal is not None and _unsafe_ip(literal):
        raise SecurityError(f"scan target blocked (non-public IP): {host!r}")

    # Reject hostnames that look like IPv4 with all-numeric labels (normalize may allow).
    if re.fullmatch(r"(\d{1,3}\.){3}\d{1,3}", h):
        try:
            v4 = ipaddress.IPv4Address(h)
            if _unsafe_ip(v4):
                raise SecurityError(f"scan target blocked (non-public IPv4): {host!r}")
        except ipaddress.AddressValueError:
            pass
