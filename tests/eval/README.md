# ReconRelate evaluation cases

This directory contains versioned ground-truth cases and graph fixtures for offline quality
measurement. The bundled `synthetic-example-v1` case tests the evaluator; it is not a statement
about ownership of the reserved example domains.

## Case format

```json
{
  "schema_version": 1,
  "case_id": "organization-and-version",
  "root_domain": "example.com",
  "labels": [
    {
      "domain": "example.net",
      "classification": "positive",
      "relationship": "owned_current",
      "source_refs": ["https://primary-source.example/evidence"]
    }
  ]
}
```

Rules:

- `positive` means the domain should be discovered for the case's declared relationship.
- `negative` means the domain is a known false association relevant to the case.
- Every label requires at least one reviewable source reference.
- The root domain must not be repeated as a label.
- Do not label an unknown domain negative merely because ownership could not be proven.
- Keep former ownership and current ownership in separate, clearly dated cases until temporal
  claims are part of the production schema.
- Use public, redistributable evidence only; do not commit commercial API responses or personal
  WHOIS data.

## Metrics

`labeled_precision` uses only predictions with positive or negative labels. Predictions absent
from the case are reported as `unlabeled_predictions` and do not affect precision. Recall is the
fraction of positive labels found. This prevents an incomplete case from silently treating every
new discovery as false, but it also means high labeled precision is not proof that unlabeled
predictions are correct; they require review and labels before making that claim.

Run an evaluation with:

```powershell
reconrelate eval tests\eval\graphs\example.graph.json `
  --case tests\eval\cases\example.json
```

## Matched provider comparisons

To measure a provider profile, run the same target with the same crawl settings, model policy, code
version, and cache state. Change only the provider profile being evaluated, then compare the graph
exports against the same case:

```powershell
reconrelate providers compare `
  --baseline free.graph.json `
  --candidate byok.graph.json `
  --case cases\organization.json `
  --json
```

Candidate-only unlabeled domains are review work, not true positives. A comparison is not eligible
to influence planner weights unless the case has at least 20 labeled domains, including 10 positive
domains, and every candidate-only prediction is labeled. These thresholds protect mechanics from
being mistaken for evidence; production thresholds can be raised as the corpus grows.

For a corpus, use a manifest whose paths are relative to the manifest file:

```json
{
  "schema_version": 1,
  "benchmark_id": "free-vs-provider-v1",
  "comparisons": [
    {"case": "cases/org-a.json", "baseline": "graphs/org-a.free.json", "candidate": "graphs/org-a.byok.json"},
    {"case": "cases/org-b.json", "baseline": "graphs/org-b.free.json", "candidate": "graphs/org-b.byok.json"}
  ]
}
```

```powershell
reconrelate providers benchmark --manifest tests\eval\benchmark.example.json
```

The benchmark uses pooled outcome counts to compute micro metrics, rejects duplicate organizations
or inconsistent policies, sums usage, and blocks planner learning if any case degrades. It does not
average per-case percentages because that would give a tiny case the same weight as a large one.
