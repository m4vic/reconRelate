# Provider data-use policy

Every ReconRelate provider declares a versioned policy that is enforced in code and shown by
`reconrelate providers --json` and `reconrelate providers doctor --json`.

| Field | Meaning |
|---|---|
| `raw_retention` | `none` or a one-way `hash_only` fingerprint; raw responses are not stored |
| `normalized_retention` | normalized evidence may live only in its run or at project scope |
| `cross_run_cache` | whether normalized provider results may be replayed into another run |
| `export_scope` | export normalized evidence, derived references only, or nothing |

Free built-in providers use `hash_only`, project-level normalized retention, cross-run cache, and
normalized export. These are explicit defaults, not permission claims about future sources.

| Provider | Raw | Normalized | Shared cache | Portable export |
|---|---|---|---|---|
| Whoxy | hash only | originating run | denied | derived claim and restricted evidence reference |

Whoxy discoveries still participate in the current run, verification, scoring, and reports. A run
configured with Whoxy neither reads nor writes the shared domain cache. Graph JSON omits Whoxy
observation field values; derived claims retain a restricted attribution and scoring reference.
Adding a paid adapter without an explicit policy raises an error during registry construction.

This is a technical safety boundary, not legal advice or a determination that redistribution is
permitted. Provider terms and applicable source restrictions still control.
