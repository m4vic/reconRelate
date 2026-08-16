# ReconRelate — measured evaluation baseline

First real precision/recall measurement against a known-truth organization, produced by the
existing offline evaluator over a real free-profile scan. This is the number the phase exit gates
have been waiting for — nearly every phase snapshot ends "quality/recall not yet measured on
held-out cases." Recorded 2026-08-16.

## How to reproduce

```powershell
# from reconrelate/, venv active, PYTHONPATH=src
python -m reconrelate.cli run automattic.com --mode quick --max-depth 1 --acquisitions --profile free
python -m reconrelate.cli export <run_id> --out <dir>
python -m reconrelate.cli eval <dir>\<run_id>.graph.json --case tests\eval\cases\automattic-v1.json --json
```

## Results

| Case | Profile | Precision | Recall | F1 | Notes |
|---|---|---|---|---|---|
| `automattic-v1` (11 pos, 1 neg) | free | **0.889** | **0.727** | **0.80** | scan served from cross-run cache (scraped 2026-08-11) |

- **True positives (8):** wordpress.com, woocommerce.com, tumblr.com, jetpack.com, akismet.com,
  pocketcasts.com, dayoneapp.com, beeper.com
- **False negatives (3):** gravatar.com, longreads.com, simplenote.com
- **False positives (1):** yahooinc.com

## What this baseline tells us (two concrete targets, not vibes)

1. **Precision leak — former-owner edges (0.889 < the ≥0.90 target).** `yahooinc.com` surfaced
   because Tumblr was *formerly* owned by Yahoo; the ownership traversal surfaced a subsidiary's
   **former** owner as a current related domain. Fix: traverse ownership **downward/current only** —
   a subsidiary's prior parent is not the target's related domain. (Same principle as the
   institutional-investor cascade fix: some ownership edges must be dead ends, not hubs.)

2. **Recall gap — missed acquisitions (0.727).** gravatar.com, longreads.com, simplenote.com are
   documented Automattic acquisitions the free path did not surface — likely absent from Wikidata as
   P856-linked subsidiaries. Fix candidates: SEC EDGAR / additional corporate sources, or
   older-acquisition coverage. Each miss is now a named regression target, not an unknown.

## Corpus status

- `cases/automattic-v1.json` — real, public-sourced (Wikipedia + official). **Starter quality;
  needs human review before it gates a release.** More known-truth orgs (Atlassian, Basecamp,
  Mozilla, …) should be added toward the plan's target of 10–20, kept precision-first.
- The cache-served scan means this measures current tool *output*, not fresh network behavior; a
  `--refresh` run is the next fidelity step.
