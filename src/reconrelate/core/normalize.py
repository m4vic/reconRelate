from __future__ import annotations

import re
from urllib.parse import urlparse

from reconrelate.core.errors import ValidationError

DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$"
)


def normalize_domain(raw: str) -> str:
    value = raw.strip().lower()
    if not value:
        raise ValidationError("domain is required")

    if "://" in value:
        value = urlparse(value).netloc
    else:
        value = value.split("/")[0]

    value = value.strip().rstrip(".")
    if not DOMAIN_RE.match(value):
        raise ValidationError(f"invalid domain: {raw}")
    return value


# Common multi-label public suffixes so eTLD+1 stays correct: foo.co.uk -> foo.co.uk,
# not co.uk. Not the full Public Suffix List — just the frequent ccTLD second levels;
# extend if a target's TLD isn't covered.
_MULTI_SUFFIXES = frozenset({
    "co.uk", "org.uk", "gov.uk", "ac.uk", "co.jp", "or.jp", "ne.jp", "com.au",
    "net.au", "org.au", "co.nz", "com.br", "com.cn", "com.mx", "co.in", "co.za",
    "com.sg", "com.hk", "com.tr", "co.kr", "com.tw",
})


def registrable_domain(host: str) -> str:
    """Best-effort eTLD+1 (registrable domain) for a hostname.

    ``blog.example.co.uk`` -> ``example.co.uk``; ``a.b.example.com`` -> ``example.com``.
    Used to collapse subdomains onto their owning apex for scope checks — ReconRelate maps
    owned *domains*, not subdomains. Curated suffix set, not the full PSL (good enough here).
    """
    host = host.strip().lower().rstrip(".")
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    if ".".join(labels[-2:]) in _MULTI_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def normalize_identifier(id_type: str, raw: str) -> str:
    value = raw.strip()
    if not value:
        raise ValidationError("identifier value is empty")

    kind = id_type.lower().strip()
    if kind == "email":
        return value.lower()
    if kind == "phone":
        digits = "".join(ch for ch in value if ch.isdigit() or ch == "+")
        if not digits:
            raise ValidationError("phone has no digits")
        return digits
    if kind == "ns":
        return normalize_domain(value)
    if kind == "tracker":
        return value.strip().upper()
    return " ".join(value.split()).lower()

