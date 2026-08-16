# Query planning and spend safety

`reconrelate plan` is an offline preflight command. It reads provider manifests and configuration,
but does not open the run database, instantiate providers, contact the network, invoke a model, or
consume billable units.

```powershell
reconrelate plan example.com --profile free
reconrelate plan example.com --budget low --history --json
reconrelate plan example.com --profile byok --approve-paid --max-billable-units 10
```

The plan separates unconditional lookups from conditional expansion and reports per-domain and
whole-run worst-case logical calls, upstream requests, and retry-reserved billable units. These are
conservative bounds, not predictions: actual work depends on discovered evidence, cache hits, and
which conditional pivots become eligible. A warning is shown when the theoretical worst case is
larger than a configured hard ceiling.

## Profiles and approval

- `free` is the default. Billable providers are excluded even if an API key is configured.
- `byok` permits configured billable providers in a preview. An actual run additionally requires
  `--approve-paid` and a positive `--max-billable-units` value.
- An API key proves configuration only. It never grants spending authority by itself.

For an approved BYOK run:

```powershell
reconrelate run example.com `
  --profile byok `
  --approve-paid `
  --max-provider-calls 500 `
  --max-billable-units 10
```

`--budget low|medium|max` controls crawl breadth and depth. It is deliberately separate from the
hard provider-call and billable-unit ceilings.

## Enforcement semantics

Every provider execution passes through the shared run budget. Before provider code or network
admission, ReconRelate reserves one logical call and the provider's worst-case billable units across
all configured retry attempts. If either ceiling would be crossed, the call fails closed with
`budget_exceeded` telemetry, zero attempts, and zero consumed billable units.

Retry allowance is conservatively reserved and is not refunded during the run. Reports show actual
provider usage beside the configured limits. This first planner increment establishes deterministic
selection and spend safety.

## Runtime evidence-gap allocation

After the initial registration, current-page, and DNS evidence is available, the runtime allocates
the limited pivot slots across two downstream gaps:

- `asset_discovery`: specific email, phone, tracker, or nameserver pivots suitable for reverse lookup.
- `corporate_control`: organization or person-name pivots suitable for corporate sources.

One best candidate for each represented gap is selected before remaining slots are filled by
utility. The versioned `pivot-utility-v1` policy uses evidence confidence, identifier specificity,
and estimated logical-call cost. Tracker utility includes the bounded current-page verification
calls required for every candidate. Each decision's gap, utility, estimated calls, and policy
version is stored in the run and rendered in reports.

This is a structural information-gain proxy, not a claim about provider recall. Provider-specific
yield probabilities will only be learned after enough labeled evaluation cases and actual usage
telemetry exist.
