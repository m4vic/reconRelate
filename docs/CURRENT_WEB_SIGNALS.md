# Current web identity signals

The built-in `http-html` provider performs one bounded root-page lookup and can follow safe,
validated redirects. It extracts relationship evidence without executing page JavaScript.

## Redirect evidence

The final response URL is preserved. When its registrable domain differs from the requested domain,
ReconRelate emits `redirects_to_domain` with the final URL as the source record. A redirect can mean
a rebrand, migration, campaign, outsourced login, or unrelated forwarding; it is evidence, not an
ownership conclusion, and the destination is not automatically queued for scanning.
It is projected as a `domain_redirects_to` candidate claim and graph edge so reports can display it,
while the durable task queue remains unchanged.

## Legal-page evidence

The root HTML is searched for same-site links whose paths explicitly identify privacy, terms,
legal, imprint/impressum, about, or company pages. At most two unique pages are fetched. External
links, product/navigation paths, fragments, and extra matches are ignored.

Text becomes `states_legal_entity` only when a label such as “legal entity,” “company name,” or
“operated by” is followed by a name with a recognized corporate suffix. The exact legal-page URL is
stored as the source record. Accepted entities receive a deterministic organization-pivot score of
0.85; generic capitalized phrases and unlabelled names do not become pivots.

Optional legal-page failures do not discard root-page evidence. Root and legal responses are each
capped at 512 KiB, parsed content is truncated to 100 KiB, the manifest permits at most three pages
and twelve requests including redirects, and all hops use the DNS-rebinding-safe transport.

## Tracker candidate verification

Recognized GA, GA4, GTM, and AdSense IDs are filtered for obvious placeholder, sample, repeated, and
all-zero values. Confidence reflects identifier family specificity; publisher IDs rank above general
analytics measurements.

A reverse-search result for a tracker is never mapped immediately. ReconRelate performs a separate
root-only fetch of every candidate and requires the exact normalized ID to still be present. Only a
match can create the relationship, graph edge, lineage, cache record, or queued task. The claim links
both the search discovery and current-page verification observations. Cached replay retains both
pieces of evidence. Mismatches and unreachable pages abstain.
