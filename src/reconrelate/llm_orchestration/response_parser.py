"""
llm_orchestration/response_parser.py

Parsing and validating LLM response JSON into structured PivotCandidate records.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from reconrelate.core.normalize import normalize_identifier
from reconrelate.core.types import ALLOWED_IDENTIFIER_TYPES, PivotCandidate

logger = logging.getLogger(__name__)


class PivotOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id_type: Literal["email", "org", "name", "ns", "phone"]
    value: str = Field(min_length=1, max_length=500)
    score: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=1_000)


class RelationshipOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    abstain: bool
    abstention_reason: str | None = Field(max_length=1_000)
    pivots: list[PivotOutput] = Field(max_length=20)

    @model_validator(mode="after")
    def decision_is_consistent(self) -> "RelationshipOutput":
        if self.abstain and self.pivots:
            raise ValueError("an abstention cannot contain pivots")
        if not self.abstain and not self.pivots:
            raise ValueError("a non-abstention requires at least one pivot")
        if self.abstain and not (self.abstention_reason or "").strip():
            raise ValueError("an abstention requires a reason")
        return self


RELATIONSHIP_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "reconrelate_relationship_pivots_v2",
        "strict": True,
        "schema": RelationshipOutput.model_json_schema(),
    },
}


@dataclass(frozen=True, slots=True)
class ParsedRelationshipOutput:
    disposition: Literal["accepted", "abstained", "invalid"]
    pivots: list[PivotCandidate]

# Generic NS providers that host millions of domains.
GENERIC_NS_DOMAINS = {
    "akam.net", "akamai.net", "akamaiedge.net",
    "cloudflare.com", "cloudflare-dns.com",
    "awsdns", "amazonaws.com",
    "googledomains.com", "google.com",
    "azure-dns.com", "azure-dns.net",
    "ultradns.com", "ultradns.net", "ultradns.org",
    "domaincontrol.com",
    "registrar-servers.com",
    "dnsmadeeasy.com",
    "nsone.net", "p01.nsone.net",
    "dynect.net",
    "he.net",
    "linode.com",
    "digitalocean.com",
    "vultr.com",
    "hetzner.com",
    "godaddy.com",
    "namecheap.com",
    "safenames.net",
}

# Generic registrar / privacy email local-parts — not useful for pivoting. Matched as a
# substring of the full email, so bare local-parts like "abusecomplaints" and "whoisrequest"
# are covered (the earlier "abuse@" missed "abusecomplaints@...").
GENERIC_EMAIL_PATTERNS = {
    "abuse@", "abusecomplaints", "hostmaster@", "postmaster@", "noc@", "admin@registrar",
    "registrar-abuse@", "whoisguard", "privacyprotect", "whoisrequest", "whois@",
    "domainsprivacy", "contactprivacy", "whoisprivacy", "domainabuse", "noreply", "no-reply",
    "registryadmin", "dns-admin", "ssl@",
    # Domain-operations role addresses. These sit on the company's OWN domain, so the
    # registrar-domain filter below does not catch them, but they identify a shared
    # registrar/DNS-operations mailbox rather than the owning entity - reverse-searching one
    # returns whatever unrelated pages happen to quote the string. Measured:
    # registrar-updates@salesforce.com pulled gov.in into a salesforce.com scan.
    "registrar-updates@", "registrar@", "domainadmin@", "domain-admin@", "domains@",
    "dnsadmin@", "domainmaster@", "domainmanager@", "tech-admin@",
}

# Local-part prefixes that mark an operational role mailbox rather than an owner contact.
# Generalizes the list above: matching the local part before "@" catches variants
# ("registrar-updates", "registrar-notices", "domain-ops") without enumerating each one.
_ROLE_LOCALPART_PREFIXES = ("registrar", "domainadmin", "domain-admin", "dnsadmin", "domainmaster")

# Domain registrars / brand-protection & privacy services. A WHOIS contact email or
# nameserver on one of these domains belongs to the REGISTRAR, not the domain owner —
# pivoting on it links every unrelated domain that happens to use the same registrar
# (this was the #1 false-positive source: markmonitor.com dragged Google domains into
# an Automattic scan). Match the *email/host domain*, not a local-part.
REGISTRAR_DOMAINS = {
    "markmonitor.com", "cscglobal.com", "csc-global.com", "cscprotectsbrands.com",
    "godaddy.com", "namecheap.com", "namebright.com", "gandi.net", "tucows.com",
    "enom.com", "enomdomains.com", "networksolutions.com", "register.com", "name.com",
    "dynadot.com", "porkbun.com", "ionos.com", "1and1.com", "ovh.com", "ovh.net",
    "hostinger.com", "hover.com", "domains.google", "googledomains.com",
    "cloudflare.com", "cloudflareregistrar.com", "amazonaws.com",
    "wildwestdomains.com", "publicdomainregistry.com", "onlinenic.com",
    "fabulous.com", "moniker.com", "web.com", "eurodns.com", "key-systems.net",
    "1api.net", "gransy.com", "safenames.net", "nom-iq.com", "comlaude.com",
}

# Hosts that dominate search results but are almost never the domain *owned* by the target
# (they surface merely because the identifier string appears on their pages). This is the
# reverse-WHOIS-via-free-search noise floor. Deliberately conservative — content platforms a
# company might actually own (wordpress/tumblr/blogspot/youtube/github) are NOT listed, so
# real ownership isn't hidden.
SEARCH_NOISE_HOSTS = {
    "baidu.com", "zhihu.com", "quora.com", "reddit.com", "wikipedia.org", "wikimedia.org",
    "stackoverflow.com", "crunchbase.com", "bloomberg.com", "glassdoor.com", "indeed.com",
    "yelp.com", "tripadvisor.com", "bing.com", "google.com", "yahoo.com", "duckduckgo.com",
    "whois.com", "who.is", "icann.org", "domaintools.com", "archive.org",
    "facebook.com", "twitter.com", "x.com", "linkedin.com", "instagram.com", "pinterest.com",
    "t.co", "goo.gl", "bit.ly",
    # IP-lookup / recon-data / threat-intel sites — they publish pages *about* every company,
    # so an org/email search surfaces them though the target doesn't own them.
    "ipinfo.io", "shodan.io", "censys.io", "virustotal.com", "securitytrails.com",
    "dnslytics.com", "builtwith.com", "similarweb.com", "netcraft.com", "robtex.com",
    "abuseipdb.com", "greynoise.io", "spyse.com", "riskiq.com", "threatcrowd.org",
    "viewdns.info", "whoxy.com", "dnsdumpster.com",
}

# File extensions a naive domain regex mistakes for a TLD (index.html, page.php, foo.js …).
_FILE_EXT_TLDS = {
    "html", "htm", "php", "asp", "aspx", "jsp", "xml", "json", "txt", "js", "css",
    "png", "jpg", "jpeg", "gif", "svg", "webp", "pdf", "doc", "docx", "zip", "md",
}


def _email_domain(email: str) -> str:
    return email.lower().partition("@")[2].strip()


def _domain_in(host: str, blocklist: set[str]) -> bool:
    """True if host equals or is a subdomain of any entry in blocklist."""
    host = host.lower().strip().rstrip(".")
    return any(host == b or host.endswith("." + b) for b in blocklist)


def is_role_mailbox(email: str) -> bool:
    """True if the local part names a domain-operations role rather than an owner contact.

    Catches the whole family (registrar-updates@, registrar-notices@, domainadmin@, ...) on a
    company's *own* domain, where is_registrar_email cannot help because the email domain is
    the target's own.
    """
    local = email.lower().partition("@")[0].strip()
    return local.startswith(_ROLE_LOCALPART_PREFIXES)


def is_registrar_email(email: str) -> bool:
    """True if the email belongs to a registrar/privacy service, or is a role mailbox."""
    return _domain_in(_email_domain(email), REGISTRAR_DOMAINS) or is_role_mailbox(email)


def is_noise_domain(domain: str) -> bool:
    """True if a *discovered* domain is junk: a file, a registrar host, or search noise."""
    from reconrelate.core.normalize import registrable_domain

    d = domain.lower().strip().rstrip(".")
    if "." not in d:
        return True
    if d.rsplit(".", 1)[-1] in _FILE_EXT_TLDS:
        return True
    apex = registrable_domain(d)
    return _domain_in(apex, SEARCH_NOISE_HOSTS) or _domain_in(apex, REGISTRAR_DOMAINS)


def parse_llm_response(content: str, source_label: str) -> ParsedRelationshipOutput:
    """Validate the complete model response; never salvage JSON from surrounding prose."""
    try:
        parsed = RelationshipOutput.model_validate_json(content)
    except ValidationError:
        logger.debug("[%s] Model output failed the strict response contract", source_label)
        return ParsedRelationshipOutput("invalid", [])
    if parsed.abstain:
        return ParsedRelationshipOutput("abstained", [])
    return ParsedRelationshipOutput(
        "accepted",
        [
            PivotCandidate(
                id_type=p.id_type,
                value=p.value,
                score=p.score,
                reason=f"LLM[{source_label}]: {p.reason}",
            )
            for p in parsed.pivots
        ],
    )


def validate_pivot(candidate: PivotCandidate) -> bool:
    """Validate pivot candidate schema, score range, and reject generic provider noise."""
    if candidate.id_type not in ALLOWED_IDENTIFIER_TYPES:
        return False
    if not candidate.value.strip():
        return False
    if not (0.0 <= candidate.score <= 1.0):
        return False

    val = candidate.value.lower()

    # Block generic nameservers (even if LLM suggests them)
    if candidate.id_type == "ns":
        if any(generic in val for generic in GENERIC_NS_DOMAINS):
            return False

    # Block generic/privacy emails, registrar-owned addresses, and operations role mailboxes
    # (even when the model proposes one - it has no reliable way to tell an owner contact from
    # a shared registrar mailbox on the company's own domain).
    if candidate.id_type == "email":
        if any(pattern in val for pattern in GENERIC_EMAIL_PATTERNS):
            return False
        if is_registrar_email(val):
            return False

    return True
