# RDAP-first registration

ReconRelate queries authoritative RDAP before using legacy WHOIS. It discovers domain services from
the [IANA RDAP DNS bootstrap registry](https://www.iana.org/assignments/rdap-dns/rdap-dns.xhtml),
following the label-wise longest-match procedure in [RFC 9224](https://www.rfc-editor.org/rfc/rfc9224.html).
Domain requests use the `domain/{name}` path defined by
[RFC 9082](https://www.rfc-editor.org/rfc/rfc9082.html), and response fields follow
[RFC 9083](https://www.rfc-editor.org/rfc/rfc9083.html).

The provider:

- accepts only HTTPS bootstrap service URLs and tries up to three authoritative alternatives;
- caches the parsed IANA registry in memory for 24 hours;
- uses the shared DNS-rebinding-safe resolver, redirect checks, response limits, and request budget;
- extracts registrant vCard identity, registration/expiration events, and nameservers;
- ignores common privacy/redaction placeholders as relationship pivots;
- follows at most one safe `related` domain link when a thin registry omits contact data;
- stores only normalized fields and compact metadata, not the unrestricted RDAP contact response.

ICANN registration-data policy permits or requires many public contact fields to be redacted. An
empty registrant identity is therefore not treated as a provider failure. If RDAP fails, returns no
domain object, or has no usable organization/name/email/phone identity, ReconRelate calls the legacy
WHOIS provider and fills only missing fields. RDAP fields retain precedence. Evidence from each
source is persisted separately, so a merged scoring record never erases provenance.

Use `RECONRELATE_SOURCE_WHOIS=rdap-iana` to prohibit legacy fallback, or
`RECONRELATE_SOURCE_WHOIS=python-whois` to explicitly use only legacy WHOIS. The default `auto`
behavior is the RDAP-first cascade.
