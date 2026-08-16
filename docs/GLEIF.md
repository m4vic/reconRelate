# GLEIF corporate hierarchy

ReconRelate can query the free official GLEIF API for Legal Entity Identifier (LEI) Level 2
relationships. No API key is required.

```console
reconrelate acquisitions "Google LLC" --source gleif
reconrelate acquisitions "Microsoft Corporation" --source gleif --json
reconrelate acquisitions "Google LLC" --source auto
```

`auto` queries every available corporate-relationship source and labels each result with its
source. A configured source can be isolated with `--source`.

When acquisition expansion is enabled for a normal scan, the runtime executes every available
corporate source independently. A failure in one does not suppress another. GLEIF hierarchy is
stored in the observation ledger, but only a source carrying explicit official-domain evidence may
enqueue a domain.

## Precision and semantics

The provider accepts a name only when it finds exactly one active, issued LEI record whose legal,
alternate, or transliterated name matches after conservative Unicode/punctuation normalization.
Fuzzy-only and ambiguous matches return no result.

GLEIF relations are named explicitly:

- `direct_accounting_parent`
- `ultimate_accounting_parent`
- `direct_accounting_child`
- `ultimate_accounting_child`

They mean accounting-consolidation hierarchy, not necessarily an acquisition, brand relationship,
operational control, or bug-bounty scope. GLEIF does not establish an official website in this
adapter, so these records do not create domain edges by themselves. Domain attachment requires
separate evidence such as Wikidata's official-website property.

Requests use the common provider request/page budgets, safe outbound HTTP transport, bounded
responses, and conservative default rate/concurrency limits. Coverage is inherently limited to
entities with LEIs and disclosed Level 2 relationships or permitted reporting exceptions.
