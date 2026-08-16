# ReconRelate Production Implementation Plan

Status: active; Phase 0 started 2026-08-14  
Target: production-grade local CLI and Python library  
Product model: open source, free-first, bring-your-own-key (BYOK) enrichment  
Primary use: authorized bug-bounty asset discovery and corporate/acquisition domain mapping

## 1. Product contract

ReconRelate accepts an authorized root domain or organization and returns a provenance-rich,
confidence-ranked map of domains and corporate relationships. The free path must be useful on
its own. Optional commercial data and cloud LLM keys improve recall, history, and difficult
entity resolution without changing the core workflow.

ReconRelate's differentiator is not access to an API. It is the efficient combination of many
sources: query only what is likely to add information, preserve all evidence, verify candidates
with independent signals, explain every conclusion, and spend paid credits or model tokens only
where they have measurable marginal value.

### Production-grade means, for this local-first product

- A new user can install it and complete a useful free scan from the README.
- A failed or rate-limited source degrades coverage; it does not silently corrupt conclusions.
- Every relationship is traceable to observations, source, collection time, and scoring policy.
- Paid calls and model calls are budgeted, previewable, attributable, cached when licensing
  permits, and disabled by default until the user supplies keys and policy.
- Results are reproducible from stored evidence and versioned rules/model configuration.
- Interrupted runs resume without duplicating chargeable calls or losing committed evidence.
- Real-world quality is measured on a versioned evaluation corpus, not by graph size.
- API keys, WHOIS personal data, and model egress have explicit security and retention controls.

### Explicitly out of scope for this plan

- Hosting a multi-tenant SaaS or paying providers on users' behalf.
- Building a global passive-DNS sensor network comparable to commercial vendors.
- Claiming legal ownership from one weak signal or from an LLM assertion.
- Vulnerability exploitation; ReconRelate discovers and ranks assets only.
- Redistributing commercial provider datasets contrary to their licenses.
- A graphical web application before the CLI/library contracts and quality gates are stable.

## 2. Success measures

The principal metric is verified useful discovery, not the number of nodes returned.

For a held-out set of organizations with known current and former assets, measure:

- Precision of `owned/current` domain claims: target >= 0.90 at high confidence.
- Precision of `acquired/current` claims: target >= 0.95 at high confidence.
- Recall relative to the documented ground-truth set, reported rather than optimized blindly.
- Unique verified domains added by each provider over the free baseline.
- Cost per unique verified domain for paid sources and LLMs.
- Abstention quality: unsupported candidates must remain candidates, not become ownership claims.
- Provenance completeness: 100% of exported claims reference stored evidence.
- Resume idempotency: no duplicate billable call after interruption at a committed checkpoint.
- Provider failure isolation: one hanging provider cannot prevent a bounded partial report.

Initial performance targets for the local CLI:

- `quick` free scan: p95 <= 2 minutes on a normal broadband connection, excluding upstream
  outages and provider-enforced waits.
- `deep` scan: explicitly long-running with visible progress, ETA-free honest status, cancel,
  checkpoint, and resume.
- Local orchestration overhead: under 10% of wall time; network dependencies should dominate.
- Default hard limits: bounded domains, pending tasks, provider calls, elapsed time, and tokens.

These targets must be revisited from measured runs before calling the product production-ready.

## 3. Architecture decision

Adopt a modular monolith. Keep one Python package, one process, and SQLite by default. Separate
the domain model, provider adapters, planning, evidence, inference, verification, persistence,
and presentation with typed contracts. Do not introduce a server, distributed queue, graph
database, or plugin process boundary until measured load requires one.

### 3.1 Core data flow

```text
Target + authorization scope + run budget
                    |
                    v
             Discovery planner
       (free first, cached before fresh,
        expected value before paid cost)
                    |
                    v
        Provider adapters / local tools
                    |
                    v
       Append-only observation ledger
     (raw ref + normalized facts + provenance)
                    |
                    v
       Candidate and claim derivation
       (rules first, LLM only for ambiguity)
                    |
                    v
          Independent verification
      (strong signal or corroborating sources)
                    |
                    v
       Derived relationship projection
       + ranked domain recommendations
```

Providers never write an `owned_by` conclusion directly. They emit observations or candidate
relationships. The inference and verification layers are the only components allowed to publish
claims. The graph and reports are rebuildable projections of the observation ledger plus a
versioned scoring policy.

### 3.2 Domain concepts

Introduce explicit typed concepts instead of overloading generic nodes and edges:

- `Entity`: legal organization, brand, person, registrar, hosting provider, or unknown entity.
- `Asset`: registrable domain, hostname, IP, netblock, certificate, tracker, nameserver, MX.
- `Observation`: a source's statement at a time, with normalized payload and raw-content hash.
- `Evidence`: an observation used for or against a candidate relationship.
- `CandidateRelationship`: unverified hypothesis that may trigger more collection.
- `Claim`: verified relationship with type, status, confidence, validity interval, and evidence.
- `ProviderCall`: request metadata, cache status, timing, quota/cost estimate, and outcome.
- `RunPolicy`: scope, budgets, egress policy, provider policy, and model policy snapshot.
- `ScoreBreakdown`: positive signals, penalties, conflicts, rule version, and calibration version.

Core claim types:

- `entity_owns_domain`
- `entity_operates_domain`
- `entity_formerly_owned_domain`
- `entity_acquired_entity`
- `entity_parent_of_entity`
- `entity_former_parent_of_entity`
- `domain_redirects_to_domain`
- `assets_share_identifier`
- `domain_resolves_to_ip`
- `domain_used_certificate`

Acquisition claims require `announced`, `completed`, `cancelled`, `divested`, or `unknown`
status and separate announcement/effective dates. Historical truth must not be overwritten by
current truth.

### 3.3 Source of truth and projections

The observation ledger is authoritative for what ReconRelate saw. Claims are authoritative only
for the selected scoring-policy version and are rebuildable. Provider caches and report files are
not sources of truth.

Store raw responses only when provider terms allow it. Otherwise store a content hash, permitted
normalized fields, source reference, retrieval time, and cache expiry. Each adapter must declare
its storage/redistribution policy.

Use SQLite in WAL mode with foreign keys, transactions, schema migrations, and bounded queries.
Keep a PostgreSQL-compatible repository boundary only if it costs little; do not build or operate
a second backend in the initial production track.

## 4. Provider system

### 4.1 Capability contract

Replace the current loosely typed registry with a typed provider protocol. A provider manifest
must declare:

- Stable provider ID and adapter version.
- Capabilities: current WHOIS, historical WHOIS, reverse WHOIS, subdomains, current DNS,
  historical DNS, reverse DNS, certificates, web history, trackers, corporate relationships.
- Whether a key is required and which environment variable supplies it.
- Free, freemium, prepaid, subscription, or local-tool tier.
- Request cost function or `unknown` when the vendor does not publish one.
- Rate/concurrency limits and supported retry classes.
- Cache TTL and whether raw/normalized responses may be retained or exported.
- Geographic/data-egress notes and terms URL.
- Health-check method and fixture-backed contract examples.

Every result uses a common envelope:

```text
ProviderResult[T]
  data
  observations
  source
  collected_at
  freshness
  completeness
  cost_actual_or_estimated
  quota_remaining_if_known
  warnings
  error (typed, optional)
```

Typed errors distinguish authentication, quota exhaustion, rate limiting, timeout, upstream
failure, malformed response, terms-disabled caching, and no results.

### 4.2 Provider optimizer

Build a deterministic query planner, not an unconstrained LLM agent. For each unresolved claim,
it ranks possible calls using:

```text
utility = expected_information_gain
          * provider_reliability
          * evidence_independence
          * freshness_fit
          / normalized_cost_and_latency
```

The initial expected-value table is hand-authored and versioned. Later, update it from evaluation
results; do not learn from unverified production output.

Planning rules:

1. Reuse permitted fresh observations before calling any dependency.
2. Run independent free sources concurrently within per-provider bulkheads.
3. Normalize and deduplicate before deciding what remains unknown.
4. Stop when the claim is verified, disproved, or the confidence ceiling cannot be raised.
5. Ask a cheap paid source only when it can resolve a material uncertainty.
6. Ask an expensive history source only when history affects the result.
7. Do not spend twice on equivalent data unless corroboration is explicitly valuable.
8. Enforce per-run, per-provider, per-domain, token, call, and wall-clock budgets.
9. Record why each call was selected or skipped.
10. Prefer partial honest results over exhausting a budget for graph expansion.

Profiles:

- `free`: no metered API or cloud model calls.
- `byok-auto`: use configured providers within a user-set budget.
- `offline`: local datasets and previously stored observations only.
- `manual`: show the plan and require approval before metered calls.
- `benchmark`: pinned providers, fixtures/models, and deterministic limits.

The CLI must provide a preflight plan showing likely providers, maximum calls, estimated spend
where knowable, data egress, cache reuse, and unknown-price warnings.

### 4.3 Provider rollout order

Free foundation:

1. RDAP-first current registration with `python-whois` fallback.
2. Existing crt.sh, HackerTarget, DNS, HTTP signals, and Wikidata adapters brought under the
   common contract.
3. Subfinder adapter with source attribution; do not silently collapse all sources into one.
4. GLEIF legal-entity and direct/ultimate parent/child relationships.
5. SEC EDGAR filings for acquisition discovery and verification.
6. Common Crawl and Wayback CDX for historical URLs, redirects, legal/footer statements,
   tracker IDs, and official-domain evidence.
7. Optional local/bulk adapters for appropriately licensed zone or DNS datasets.

Paid/BYOK adapters, only after the free evaluation baseline:

1. Whoxy current/history/reverse WHOIS (already partially implemented).
2. DNSlytics reverse tracker, IP, NS, MX, and history.
3. WhoisXML DRS capability family.
4. SecurityTrails passive DNS and domain search.
5. DomainTools/DNSDB only when a contributor or user can test the licensed API contract.

An untested adapter must be marked experimental. It cannot be advertised as active merely
because its response schema was inferred from documentation.

## 5. Relationship inference and verification

### 5.1 Evidence strengths

Start with explicit, explainable rules and calibrate them on the evaluation corpus.

Strong positive evidence examples:

- Official registry/filing relation plus verified official website.
- Unique corporate registrant email corroborated by current site/legal identity.
- Unique tracker ID across a small, coherent set of sites plus corporate corroboration.
- Completed acquisition filing and target-domain identity match.
- Domain listed by the organization in an official filing or controlled-domain document.

Medium evidence examples:

- Historical vanity nameserver or MX with bounded rarity.
- Certificate organization/subject and SAN relationship with temporal agreement.
- Official redirects, legal footers, privacy-policy controller statements, or cross-links.
- GLEIF direct/ultimate consolidation relationship.

Weak discovery-only evidence examples:

- Shared IP, generic CDN, registrar, public DNS provider, generic MX, or shared SaaS platform.
- News text without an official source.
- Similar names, page titles, or favicon hashes alone.
- LLM-generated association without external evidence.

Negative evidence and conflicts must lower confidence: sale/divestiture, unrelated legal entity,
generic infrastructure, privacy proxy, parked domain, expired observation, contradictory official
source, or an out-of-scope target.

### 5.2 Verification policy

- Publish a high-confidence ownership/operation claim only with one authoritative strong signal
  or two independent medium signals.
- Never treat two observations derived from the same upstream dataset as independent.
- Model source lineage so aggregators do not create false corroboration.
- Keep unresolved candidates visible in a separate section with the next evidence needed.
- Preserve first-seen, last-seen, valid-from, valid-to, and observed-at separately.
- Apply rarity/cardinality penalties: a tracker shared by 3 sites differs from an IP shared by
  50,000 sites.
- Calibrate confidence from measured precision; do not present arbitrary rule weights as
  statistical probability.

### 5.3 Acquisition pipeline

1. Resolve the input organization to legal entities and aliases using GLEIF/Wikidata.
2. Gather structured parent/child relationships.
3. Discover transactions from SEC submissions and official investor-relations material.
4. Use financial news only to propose candidates and locate primary sources.
5. Extract acquirer, target, status, dates, percentage, consideration, and source citations.
6. Resolve each entity to official domains using registry identifiers, official websites,
   filings, redirects, and legal-page evidence.
7. Create temporal entity claims before domain claims.
8. Re-evaluate domain ownership after divestiture, cancellation, or rename events.

## 6. LLM architecture

LLMs are optional hypothesis and extraction components, never the authority for a claim.

### 6.1 Model gateway and tasks

Keep LiteLLM behind a ReconRelate-owned `ModelGateway` with typed tasks:

- legal-entity extraction from filing text
- acquisition-event extraction
- ambiguous entity resolution
- relationship-evidence classification
- evidence conflict summary

Do not use an LLM for regex extraction, normalization, DNS work, provider selection rules, budget
enforcement, or final claim validation.

### 6.2 Model policy

- Select models by task-specific eval results, not by a permanently hardcoded "latest" alias.
- Ship a remotely updateable or release-versioned recommendation catalog containing provider,
  model ID, snapshot where available, context, price metadata, and last evaluation date.
- Never change a user's pinned model automatically.
- Recommend a current economical model for extraction and a stronger model only for unresolved
  adjudication; allow Ollama and other LiteLLM providers.
- Support `local-only`, `cloud-allowed`, and `cloud-redacted` egress modes.
- Redact unnecessary WHOIS personal data before cloud calls.
- Use structured outputs/JSON Schema, strict validation, bounded repair, and safe abstention.
- Version system prompts, schemas, model settings, and evidence-compaction rules.
- Cap tokens, reasoning effort, calls, retries, and spend per run.
- Do not retry a chargeable ambiguous timeout without consulting provider idempotency semantics
  or recording the potential duplicate charge.

### 6.3 LLM evaluation

Build a versioned corpus containing public, legally usable examples of:

- straightforward corporate WHOIS
- privacy-protected WHOIS
- registrar and hosting noise
- completed, announced, cancelled, and divested acquisitions
- entities with similar names
- multilingual legal pages
- prompt-injection text embedded in fetched pages/filings
- missing evidence requiring abstention

Compare deterministic-only, local model, economical cloud model, and stronger escalation model.
Record extraction accuracy, claim precision impact, abstention, latency, and token cost. A model
recommendation changes only through this evaluation.

## 7. Persistence and migrations

Introduce versioned migrations before changing the schema. Use expand-contract changes and test
upgrade from a copy of the current database.

Proposed tables/projections:

- `schema_migrations`
- `runs` and immutable `run_policy_json`
- `tasks` with durable state, attempts, lease/checkpoint, and idempotency key
- `provider_calls`
- `entities`
- `assets`
- `observations`
- `observation_artifacts` or permitted raw-response references
- `candidate_relationships`
- `claims`
- `claim_evidence`
- `score_breakdowns`
- `source_lineage`
- `provider_health_snapshots`
- `budgets` and `usage_events`

Retain compatibility views or an explicit one-time migration for current nodes/edges. Preserve
the current DB before migration and prove rollback/restore on production-shaped data.

Default retention proposal:

- Claims and normalized public observations: retained until user deletion.
- Raw WHOIS/HTML: 30 days by default, configurable, minimized, and excluded when terms forbid.
- Provider-call metadata without secrets: retained with the run.
- Logs: 14 days locally by default.
- Secrets: never stored in the database or artifacts.

The user must approve final retention defaults before release because WHOIS may contain personal
data and jurisdictions differ.

## 8. Reliability and operations

### 8.1 Execution model

Use a durable local task queue in SQLite rather than an in-memory-only queue. Each task has a
stable idempotency key based on run, capability, normalized subject, provider, and policy version.
Commit observations and task completion atomically. On restart, expired in-progress leases return
to pending without duplicating already committed results.

### 8.2 Dependency controls

For every provider and model:

- explicit connect/read/total timeout shorter than the containing domain/run deadline
- provider-specific concurrency semaphore and rate limiter
- capped exponential backoff with jitter for retryable idempotent failures
- no retries for authentication, invalid request, or exhausted budget
- circuit breaker with cool-down and visible degraded state
- bounded response size and pagination
- per-provider bulkhead so one slow source cannot consume all workers
- structured health and quota telemetry

Overload behavior is explicit: stop scheduling low-utility enrichment, preserve queued work, and
produce a partial report. Never grow an unbounded queue.

### 8.3 Backup, restore, rollback, and runbooks

Before production-grade status:

- implement consistent SQLite backup and integrity check
- execute and document a restore into a scratch location
- execute a schema/code rollback rehearsal
- provide runbooks for corrupt DB, provider outage/rate limit, runaway cost, interrupted migration,
  and bad scoring/model release
- provide kill switches for every paid provider, all cloud LLMs, acquisition expansion, and
  active network probing

Because the target is a local CLI, 24/7 uptime and paging are not applicable initially. The
equivalent operational goal is recoverable runs, actionable errors, diagnosable artifacts, and
no silent corruption or unbounded external spend.

## 9. Security, privacy, licensing, and abuse controls

- Keep and extend SSRF protection; validate after every redirect and DNS resolution, including
  DNS rebinding defenses and IPv4/IPv6 special ranges.
- Default to passive collection. Any active probing must be separately enabled and scope-checked.
- Store keys in environment variables or OS keyring; deprecate plaintext persistent key values.
- Mask secrets in config output, logs, traces, exceptions, and diagnostic bundles.
- Never send raw provider keys or unrestricted raw WHOIS blobs to an LLM.
- Treat all fetched pages and filings as untrusted data, not model instructions.
- Validate model output and provider output against schemas and target scope.
- Add per-run authorization acknowledgement and preserve scope in the run policy.
- Add dependency lock/reproducible environment, secret scanning, dependency audit, static analysis,
  and release artifact checks.
- Maintain a provider terms matrix covering caching, derived data, attribution, commercial use,
  redistribution, and deletion obligations.
- Export provider-derived data only as permitted; otherwise export the claim, citation/reference,
  and permitted normalized fields.
- Document responsible-use boundaries and a security reporting process.

This plan identifies licensing and privacy work but does not substitute for legal advice.

## 10. CLI and library experience

Keep the common free path one command:

```text
reconrelate example.com
```

Add commands/flags in backward-compatible stages:

```text
reconrelate plan example.com --profile free
reconrelate plan example.com --profile byok-auto --max-cost 1.00
reconrelate run example.com --profile free
reconrelate run example.com --profile byok-auto --max-cost 1.00
reconrelate explain <run-id> <domain>
reconrelate providers list
reconrelate providers doctor
reconrelate providers terms
reconrelate models recommend
reconrelate eval run
reconrelate db backup|restore|check
```

UX requirements:

- Human-readable output on a terminal and stable versioned JSON/JSONL for pipelines.
- Source, confidence class, temporal status, and evidence count visible by default.
- Cost/credit estimate before paid execution and actual usage afterward.
- Clear distinction among verified, probable, candidate, rejected, and out-of-scope domains.
- Honest partial/degraded status listing unavailable providers and resulting coverage gaps.
- Progress by stages, completed tasks, pending tasks, calls, cache hits, and budget—not fabricated ETA.
- Ctrl+C checkpoint/resume with consistent state.
- `NO_COLOR`, quiet, verbose, and non-interactive modes.
- Stable exit codes for validation, partial success, provider auth, budget exhaustion, storage,
  interruption, and internal errors.

Publish a small Python library contract only after internal modules stabilize. Provider authors
should be able to implement a documented protocol and run a contract test kit.

## 11. Observability

Local-first observability must be useful without running Prometheus.

- Structured JSON event log option plus concise human logs.
- Run ID, task ID, provider call ID, and claim ID correlation.
- Per-stage and per-provider latency p50/p95/p99 in the final run metrics.
- Counts for scheduled/completed/failed/retried calls, cache hits, circuit-open skips, claims by
  confidence, rejected candidates, and abstentions.
- Estimated and actual API/model cost where available.
- Provider health summary and quota snapshot.
- Privacy-safe diagnostic bundle command with explicit preview.
- Optional OpenTelemetry hooks later; no hosted telemetry by default.

The key product-health metric is high-confidence verified domains per run, accompanied by the
rejection/abstention rate and data-source coverage—not total graph size.

## 12. Test and evaluation strategy

### 12.1 Test layers

- Unit: normalization, scoring, rarity penalties, temporal logic, budgets, planner utility,
  redaction, SSRF and output validation.
- Database integration: real SQLite migrations, constraints, transactions, WAL behavior,
  interruption, resume, duplicate delivery, backup, restore, and rollback.
- Provider contracts: recorded/synthetic licensed fixtures for success, empty, malformed,
  paginated, 401, 429, 500, timeout, oversized response, and schema drift.
- Planner integration: prove free-before-paid, cache-before-call, stop-on-verification,
  independent-corroboration, and hard budget enforcement.
- LLM evaluation: fixed corpus and schemas; compare against deterministic baseline.
- CLI end to end: clean install, free scan, partial provider failure, interrupt/resume, export.
- Opt-in live canaries: a small set of stable public/owned test targets with strict limits.
- Security: SSRF redirect/rebinding cases, prompt injection, secret leakage, unsafe paths,
  dependency and license scanning.

Tests must assert observable behavior. External providers are mocked at their HTTP boundary, not
by mocking ReconRelate's own repository or inference logic. Periodic opt-in live contract checks
detect provider drift.

### 12.2 Ground-truth corpus

Create `tests/eval/` with versioned cases and provenance. Begin with 10–20 organizations spanning:

- public and private entities
- straightforward and privacy-protected registration
- current subsidiaries, acquisitions, former subsidiaries, and cancelled deals
- shared hosting/CDN/registrar noise
- tracker reuse and generic tracker false positives
- multiple countries and naming systems

Keep discovery and held-out evaluation sets separate. Record unknowns rather than forcing labels.
Every real false positive becomes a regression case.

### 12.3 Release gates

A release cannot be called production-grade until:

- full test suite passes from a clean documented environment
- free end-to-end scan succeeds against authorized real targets
- representative provider failure and hang tests prove bounded degradation
- evaluation thresholds are met and published with corpus version
- migration from the existing DB, backup, restore, and rollback are exercised
- secrets/license/PII checks pass
- cost budgets are enforced under retry and interruption
- a new user completes the README workflow without builder assistance

## 13. Delivery phases

Each phase is a vertical slice with a falsifiable exit gate. Do not begin broad provider expansion
until the evidence and evaluation foundations are in place.

### Phase 0 — Baseline and reproducibility

Progress snapshot (2026-08-14): the declared development extras install successfully on the
local Python 3.14 environment; all 82 tests pass; package metadata exposes version
`0.1.0`; the README/environment template no longer contradict current LLM and run-mode behavior;
local secrets, databases, environments, caches, and artifacts now have `.gitignore` coverage;
and a versioned, provenance-required offline evaluator measures labeled precision, recall, F1,
known false positives, false negatives, and unlabeled discoveries from saved graph exports.
Real authorized scan quality measurement, non-synthetic corpus cases, broader static tooling,
and clean-machine verification remain open.

Deliver:

- Repair the development environment and pin supported Python versions/dependencies.
- Make the existing 76-test suite runnable locally and in CI; add formatting, lint, typing, secret
  scan, dependency audit, and package build.
- Run bounded authorized free scans and label current false positives/negatives.
- Establish the first ground-truth corpus and baseline metrics.
- Reconcile README, guide, and actual behavior; fix encoding corruption.

Exit gate: clean install plus current free scan is reproducible, and baseline precision/recall,
latency, provider failures, and cost are recorded. No claim of improvement before this baseline.

### Phase 1 — Observation and evidence foundation

Progress snapshot (2026-08-14): Phase 1's durable first slice is implemented. Startup now applies
checksum-verified, immutable numbered SQLite migrations in atomic transactions. Migration tests
cover fresh databases, legacy-schema upgrades with row preservation, repeat startup, checksum
drift, and rollback after a failed statement. The first migration adds append-only observations,
derived claims, supporting/contradicting claim evidence, and source-lineage storage. Typed models
validate confidence values and generate stable idempotency keys; repository APIs support replay-safe
writes, historical snapshots, filtered observation reads, and end-to-end claim provenance queries.
The SQL schema and migrations are included in built wheels. Current WHOIS, HTML, DNS, subdomain,
reverse-WHOIS, and Wikidata acquisition results now cross a typed `ProviderResult` boundary and
are normalized into source-attributed observations. Registry-selected adapters carry their exact
provider identity, including BYOK replacements. Graph JSON exports include observations and claims,
while Markdown reports summarize evidence sources. A deterministic `relationship-v1` policy now
projects subdomain, reverse-WHOIS, and acquisition discoveries into confidence-classified claims
linked to their supporting observations. Cache records retain serialized evidence, and zero-network
replay reproduces claim type, score, source, evidence predicate, and acquisition graph topology;
older cache entries receive explicit legacy-cache evidence instead of untraceable edges. Repository
batches rollback instead of committing when ingestion fails. A second migration stores the complete
normalized observation set in the cross-run cache, so WHOIS, HTML, DNS, and relationship evidence
survive zero-network replay. IPv4, IPv6, MX, nameserver, CNAME, and identifier-pivot edges now have
evidence-linked claims; identifier claims prefer their underlying WHOIS/HTML observation and clearly
fall back to a relationship-engine observation when no direct source fact exists. Exports include a
deterministic `claim_projection` graph rebuilt solely from claims and evidence, independent of legacy
node/edge UUIDs. The full suite has 100 passing tests, including fresh-versus-cached projection
equivalence. Built wheels contain both migrations, and a real CLI JSON check confirms an
infrastructure claim rebuilds with its evidence source. The CLI now provides database-only `check`,
consistent online `backup`, guarded `restore`, and explicit-cutoff `retention` operations. Restore
rejects corrupt/non-ReconRelate candidates and preserves a verified pre-restore safety backup;
retention previews by default, requires `--apply --yes`, backs up automatically, and deletes a run's
graph/ledger/evidence transactionally. Unit tests and real installed-CLI rehearsals cover backup,
mutation, restore, rollback copy preservation, corrupt input, retention preview/apply, and foreign-key
integrity. A database operations runbook documents recovery and rollback. Final retention defaults,
a production-shaped schema/code downgrade rehearsal, and a destructive claim-projection rebuild
remain open; therefore the Phase 1 exit gate is not yet met.

Deliver:

- Add migrations and the observation/evidence/claim schema.
- Add provenance, source lineage, timestamps, raw-data retention policy, and rebuildable graph.
- Adapt existing providers to `ProviderResult` without changing user-facing discovery behavior.
- Implement verified/candidate/rejected states and explainable score breakdowns.
- Migrate existing data with backup/restore tests.

Exit gate: every exported relationship points to evidence; deleting and rebuilding claim
projections yields equivalent results.

### Phase 2 — Provider contracts and resilient execution

Progress snapshot (2026-08-14): the first provider-execution slice is implemented. Registry
manifests now declare operations, result contracts, required configuration, billing behavior, and
billing units. A shared async executor enforces deadlines, bounded retry counts, result validation,
typed auth/rate-limit/malformed/timeout errors, and per-provider circuit breaking. WHOIS, HTML, DNS,
subdomain waterfall, reverse-WHOIS, and acquisition calls use that executor. Migration 0003 adds
per-run provider-call accounting for status, attempts, latency, billable units, error class, and
timestamps; exports and Markdown reports expose aggregated usage. Runs with exhausted provider
failures complete as `completed_degraded` while retaining successful partial evidence. The
configuration-only `providers doctor` reports manifest/key readiness with zero network and billable
calls. Adapter behavior was corrected so transport, malformed, rate-limit, and paid-provider auth
failures are not silently represented as valid empty results. The suite has 112 passing tests,
including hangs, 429s, malformed data, circuit opening, failed telemetry storage, degraded partial
runs, and conservative paid-attempt accounting. Migration 0004 replaces the process-only BFS work
source with run-scoped SQLite tasks using idempotent enqueue, atomic cross-connection claims, bounded
attempts, leases, expired-lease recovery, and terminal failure state. The orchestrator commits its
processed marker only after evidence, graph, and cache work finishes, then completes the task;
crash-abandoned `running` runs resume the original run ID and immediately reclaim unfinished leases.
Reports expose task-state counts, and partial/degraded states distinguish unfinished or exhausted
work. Migration 0005 persists consecutive provider failures and circuit-open deadlines in SQLite,
so independent executors sharing a database stop calling an unhealthy source. Authentication
failures open immediately to protect paid usage. Manifest concurrency limits drive per-provider
async bulkheads, and the comma-separated `RECONRELATE_DISABLE_PROVIDERS` kill switch removes selected
adapters without calls or paid units while the run continues. The suite has 122 passing tests,
including separate-connection task claims, a simulated process crash, cross-executor circuit state,
concurrency-ceiling measurement, immediate auth shutdown, and an all-providers-disabled partial-data
run. Migration 0006 adds fixed-window request accounting and expiring concurrency leases, acquired
atomically in SQLite before every real provider attempt. Separate processes therefore share the same
ceilings; crashed leases expire, local rejection records zero paid attempts/units, and capacity
pressure does not poison upstream circuit health. Manifest defaults are visible in provider doctor
and adjustable per provider through environment variables. The suite has 127 passing tests,
including multi-connection concurrency contention, shared rate exhaustion, stale-lease reclamation,
and billing accuracy under local rejection. Provider manifests now declare byte and item ceilings;
all direct HTTP adapters use a shared streaming reader that rejects oversized `Content-Length` or
chunked bodies, and the executor independently bounds normalized collection sizes and serialized
bytes. Silent CT truncation and unbounded HTML/API reads were removed, and Wikidata no longer nests
its own retry loop under executor retries. Response-limit violations are typed, non-retryable, and
reported as malformed provider calls. The suite has 132 passing tests, including declared-length,
stream overflow, malformed JSON, item-count, and normalized-byte enforcement. Explicit live health
probes are now explicit through `providers doctor --live --target`: supported free adapters execute
concurrently with normal time/response bounds, while paid and special-input providers are skipped
before construction. Output contains health state, latency, and counts but no evidence payload or
PII; the default doctor remains zero-network. The suite has 135 passing tests, including paid-provider
non-instantiation, parallel probe execution, empty/error states, unsafe-target rejection, and CLI
opt-in gating. The shared HTTP transport now validates the exact DNS answers passed to aiohttp,
fails closed on mixed public/private answers, and manually validates every redirect hop. URL
credentials, non-HTTP schemes, unsafe address ranges, redirect loops beyond five hops, and HTTPS to
HTTP downgrades are rejected before another request. All direct HTTP adapters use this transport,
and security failures are non-retryable. The suite has 151 passing tests. At this point fair waiting,
multi-host coordination, adapter-specific pagination budgets, and sockets owned by third-party SDKs
remained open; the local-process items are addressed by the increments below.

Migration 0007 replaces immediate cross-process capacity rejection with a durable FIFO waiter
queue. Each execution receives one stable request ID, joins the provider queue once, and may
acquire a lease only while it is the oldest live waiter. Queue waiting is bounded, cancellable, and
separate from provider retry accounting: it performs no network I/O, consumes no paid units, and
does not affect circuit state. Expired waiters are reclaimed transactionally so crashed processes
cannot block the queue. The existing fixed-window rate accounting remains conservative; a queued
request is charged only when it receives both rate capacity and a concurrency lease. The wait budget
is configurable with `RECONRELATE_PROVIDER_CAPACITY_WAIT_SEC`. The suite has 155 passing tests,
including multi-connection FIFO ordering, stale waiter reclamation, bounded-wait billing, and
cancellation cleanup. A runtime check with two independent SQLite connections confirmed one queued
waiter, successful handoff, one provider attempt, one paid unit, and no leaked waiter.

Migration 0008 and the provider-budget context make nested transport work visible and bounded. Every
manifest declares maximum upstream requests and pages per provider attempt. The executor installs an
async-local budget around the adapter call; the guarded HTTP transport consumes one request before
each connection, and paginated adapters explicitly consume one page before parsing it. Crossing a
budget fails closed before the next request. Telemetry persists actual upstream-request and page
counts separately from logical attempts, so retries, latency, and paid usage can be audited without
pretending a multi-request adapter was one network call. SDK-owned transports must explicitly consume
their opaque request allowance at their call boundary until deeper hooks are available. Built-in
HTTP, WHOIS, DNS, and search adapters are instrumented. The suite has 160 passing tests, including
retry reset, async-context isolation, redirect preflight enforcement, non-retryable overflow, and
durable telemetry. An installed-package runtime execution persisted one logical attempt, two nested
requests, one page, and successful status in SQLite without external network access.

The final Phase 2 transport increment moves blocking third-party WHOIS and web-search SDK calls out
of executor threads and into short-lived worker processes. The parent launches a fixed operation
without a shell, sends sensitive lookup input over stdin (never argv), accepts only bounded JSON,
and terminates then kills the worker on timeout or cancellation. The worker suppresses dependency
stdout so logs cannot corrupt or inflate the protocol. Provider request/page budgets are consumed in
the parent at the opaque SDK boundary. Missing optional dependencies return explicit unavailable
metadata rather than fabricated WHOIS identifiers, and worker failures remain typed provider errors.
DNS uses the same boundary, eliminating all `asyncio.to_thread` calls from provider adapters. The
worker inherits only an allowlist of operating-system, certificate, locale, and proxy variables—not
provider or model secrets. The suite has 168 passing tests, including real subprocess protocol,
environment stripping, forced deadline termination, cancellation cleanup, operation allowlisting,
adapter normalization, and the no-fabrication WHOIS behavior. An installed-package runtime check
confirmed a child did not inherit `OPENAI_API_KEY` and was terminated in 64 ms after a forced 50 ms
deadline. The default provider doctor remains at zero network and billable calls.

Deliver:

- Typed manifests, errors, capability interfaces, adapter contract kit, health checks.
- Durable SQLite task queue and idempotent resume.
- Timeouts, bulkheads, rate limits, bounded retries, circuit breakers, response caps.
- Provider doctor, partial/degraded status, usage accounting, and kill switches.

Exit gate: injected hangs, 429s, malformed responses, crashes, and resumes do not exceed budgets,
duplicate committed calls, or prevent a partial report.

### Phase 3 — Free intelligence expansion

RDAP-first registration increment: add an `rdap-iana` provider that retrieves the IANA DNS bootstrap
registry over the guarded HTTPS transport, caches the parsed registry for 24 hours in-process, uses
RFC 9224 label-wise longest matching, and constructs the RFC 9082 `domain/{name}` query. Only HTTPS
service bases are accepted. Responses are normalized from RFC 9083 domain objects into the existing
registration record without retaining unrestricted raw contact payloads; redaction placeholders are
never emitted as identity pivots. One safe `related` domain link may be followed for thin-registry
responses. Orchestration receives an ordered registration-provider cascade rather than one selected
provider. It persists RDAP and legacy WHOIS observations independently, merges missing fields for
downstream scoring with RDAP precedence, and invokes legacy WHOIS only when RDAP errors, returns
empty, or lacks an organization/name/email/phone identity pivot. Provider telemetry and budgets stay
separate for each source. A source pin still selects exactly one registration provider.

Progress snapshot (2026-08-14): `rdap-iana` is registered ahead of `python-whois`; automatic runtime
construction supplies both as a cascade, while `RECONRELATE_SOURCE_WHOIS` pins exactly one. The
provider accepts up to three HTTPS services from the longest bootstrap match, normalizes registrant
jCard fields, events, nameservers, status, and thin-registry related responses, and removes common
privacy placeholders. Orchestration keeps source-specific results for evidence and telemetry while
merging fields RDAP-first for scoring. The suite has 179 passing tests covering thick, thin,
redacted, alternate-service, missing, malformed, cache, cascade-stop, fallback, source pin, and
full-run provenance cases. A real lookup of the reserved `example.com` domain resolved through IANA
to `rdap.verisign.com`, produced nameservers and lifecycle dates with no public identity, and used
two bounded requests/pages. The source's unique relationship-recall contribution on non-synthetic
held-out organizations is not yet measured, so the Phase 3 exit gate remains open.

Subfinder increment: treat the ProjectDiscovery binary as an optional local provider discovered from
`RECONRELATE_SUBFINDER_PATH` or `PATH`. Run it without a shell, disable update checks and active
resolution, send one validated domain, impose outer wall-clock and bounded stdout/stderr limits, and
request JSONL with collected passive-source attribution. Default sources are an explicit no-key
allowlist; configured/API-backed sources require an explicit ReconRelate environment override so an
existing Subfinder key file cannot silently introduce paid calls. Normalize output into typed
subdomain findings whose source list produces separately attributed immutable observations. A domain claim
links every source observation rather than overwriting all but one. If the executable is absent,
unhealthy, empty, or times out, the existing crt.sh/HackerTarget waterfall remains available.

Progress snapshot (2026-08-14): executable-aware manifests now report `dependency_missing` without
launching a process, and Subfinder becomes the first waterfall source only when an executable path
is usable. Its adapter uses passive JSONL plus `collect-sources`, an explicit four-source no-key
default, update/active/all-mode suppression, source/rate validation, 4 MiB stdout and 64 KiB stderr
ceilings, a 20-second process deadline, termination on timeout/cancellation, and a 25-second
manifest-level outer deadline. Per-source `SubdomainFinding` values create separately attributed observations;
the subdomain claim now links every observation instead of overwriting duplicates. The suite has
190 passing tests covering parsing, scope filtering, source merging, caps, malformed output,
executable detection, safe flags, budget accounting, timeout/cancellation, built-in fallback,
multi-source evidence, and provider-specific deadlines. The default doctor remains at zero network
and billable calls and accurately reports Subfinder unavailable on this machine. No real Subfinder
enumeration was run because the binary is not installed; source-quality lift remains unverified.

Deliver in measured increments:

- RDAP-first registration.
- Subfinder integration with per-source provenance.
- GLEIF corporate hierarchy.
- SEC acquisition ingestion and primary-source extraction.
- Common Crawl/Wayback historical web evidence.
- Improved tracker, legal-page, redirect, certificate, and rarity signals.

Exit gate: each source demonstrates unique verified recall improvement on held-out cases without
dropping high-confidence precision below target. Sources that do not help are removed or demoted.

GLEIF implementation boundary (2026-08-14): use the free official GLEIF API as an independent
corporate-hierarchy source. Resolve an organization only when exactly one active/issued LEI record
has an exact normalized legal/alternate name match; ambiguous matches abstain. Emit direct and
ultimate accounting-consolidation parent/child relations with LEI identifiers and relationship
record provenance. Do not label these relations as acquisitions and do not invent an org-to-domain
edge: GLEIF establishes legal-entity hierarchy, while a separate source such as Wikidata P856 must
establish an official domain. Query and result counts remain inside provider budgets. The first
increment exposes the source through the provider registry and acquisitions CLI; orchestration will
then combine all selected relationship sources instead of replacing one with another.

GLEIF orchestration progress (2026-08-14): the runtime now selects all available corporate
relationship providers rather than one source. Each source receives independent execution,
telemetry, request/page limits, failure isolation, and per-provider expansion deduplication.
Domainless GLEIF hierarchy is retained as immutable organization-to-organization evidence with LEI
record identifiers but cannot enqueue a domain. Wikidata may still attach a related organization to
a domain only through its explicit P856 official-website statement. Automated coverage verifies
multi-source execution, independent failure, evidence persistence, and the no-domain/no-scan-edge
invariant. A real persisted orchestration run for `Google LLC` executed both `wikidata` and `gleif`,
stored six GLEIF organization observations, stored zero GLEIF domain observations, and enqueued
domains only from Wikidata official-website evidence. The suite has 197 passing tests. Remaining
gate: measure cross-source corroboration and unique recall on held-out cases.

SEC ingestion boundary (2026-08-14): add a free `sec-edgar` corporate source using only official
SEC endpoints. Require a user-supplied declared `RECONRELATE_SEC_USER_AGENT` containing the
operator's contact address; no API key is required. Resolve a filer by a unique exact normalized
SEC company title, inspect a bounded recent set of Forms 8-K/8-K-A whose item metadata includes
Item 2.01, and read only the primary filing document. Emit an `acquired` organization relation only
for explicit completed/closed/consummated acquisition language that identifies a legal-entity name;
proposals, agreements, dispositions, generic assets, and ambiguous extractions abstain. Preserve CIK,
accession, filing date, filing URL, and the bounded supporting sentence as provenance. SEC evidence
never creates a domain edge without an independent official org-to-domain source. Respect SEC fair
access with a conservative internal throttle below the published maximum, bounded documents and
filing count, and the shared provider request/page/timeout budgets.

SEC ingestion progress (2026-08-14): `sec-edgar` is registered as a free, contact-declared source;
doctor reports it as `configuration_missing` until a User-Agent is supplied and
`configuration_invalid` when the value lacks an email-like contact. The adapter implements exact
CIK resolution, bounded recent Item 2.01 selection, primary-document reads, direct completion rules,
and a two-statement rule for filings that name the target in Item 1.01 then confirm completion in
Item 2.01. CIK, accession, date, URL, and supporting text survive orchestration into the immutable
observation. Tests cover proposals/dispositions abstention, ambiguity, form/item filtering, source
limits, contact validation, and the no-domain-edge invariant. Official SEC filing
`0001213900-26-039507` was used to validate the cross-reference rule against real filing language;
a live adapter call remains intentionally unrun until the owner supplies a real SEC contact value.

Wayback implementation boundary (2026-08-14): add a free `wayback` historical-web capability using
the Internet Archive CDX API and replay service. Query only exact bare/www root URLs, request a
bounded earliest/latest sample of successful HTML captures collapsed by digest, deduplicate across
queries, and fetch at most four snapshots. Normalize archived titles, tracker IDs, copyright entity,
capture timestamp, original URL, digest, and immutable replay URL into typed records. Historical
signals are time-scoped observations and must not be treated as proof of current ownership or
silently become current reverse-search pivots. The first increment exposes a dedicated CLI query and
provider contract; normal-scan use remains opt-in until the evaluation corpus measures whether the
extra calls improve unique verified recall. Apply shared safe HTTP, response, request/page, timeout,
and provider concurrency/rate budgets. Common Crawl follows as an independent source only after the
Wayback increment is measured, so correlated archive evidence is not prematurely double-counted.

Wayback progress (2026-08-14): the typed adapter, provider manifest, `history` CLI, opt-in scan flag,
settings/config surface, and time-scoped observation normalization are implemented. The full suite
has 214 passing tests. Coverage includes CDX
contract validation, status/MIME filtering, host scope, HTML signal extraction, earliest/latest
selection, one-result slicing, explicit historical predicates, and disabled/enabled normal gather.
The real CDX endpoint returned HTTP 200 through the operating-system HTTP client, but the Python
adapter correctly rejected an ISP-presented self-signed certificate chain on this machine. No TLS
verification bypass was added; live replay quality remains unverified until a trusted CA chain is
available. Common Crawl remains deferred until the Wayback evidence-quality gate is measurable.

Current-web identity increment (2026-08-14): extend the existing bounded HTML provider rather than
add another root fetch. Preserve the final response URL and emit a cross-registrable-domain redirect
observation without treating it as ownership or automatically enqueueing the destination. Parse
same-site links for an explicit allowlist of privacy, terms, legal, imprint, and about paths; fetch
at most two unique legal pages and extract only labelled legal-entity/operator phrases ending in a
recognized corporate suffix. Retain the source legal-page URL for each entity. Legal-page entities
may become high-quality organization pivots, while generic title words, navigation labels, social
links, and unlabelled capitalized text remain excluded. Response and page budgets must include these
extra reads, all redirect hops continue through the rebinding-safe transport, and provider failure
on an optional legal page must not discard already-collected root evidence. Verify against real
public sites before describing the signal as useful.

Current-web progress (2026-08-14): final URL/cross-domain redirect fields, same-site legal-link
selection, two-page bounded retrieval, labelled corporate-entity extraction, source-URL provenance,
provider observations, and deterministic 0.85 organization pivots are implemented. Optional legal
page errors preserve root evidence; the manifest now accounts for three content pages and uses a
25-second outer deadline. Tests cover same-site/path filtering, external/noise rejection, corporate
suffix and label requirements, redirect abstention from expansion, sourced observations, pivot
strength, visible-but-not-enqueued redirect claims, and real Unicode copyright marks. The full suite
has 220 passing tests. A real
`example.com` lookup verified production transport and correct empty-signal abstention, returning
the final HTTPS URL and no redirect/legal/tracker/copyright claims. Positive-signal precision still
requires authorized real-site evaluation before the phase gate is met.

Tracker verification increment (2026-08-14): treat search-engine reverse results as candidates, not
facts. For tracker pivots, every candidate domain must pass a fresh bounded root-page verification
through the existing safe HTML provider and contain the exact normalized tracker ID before a
relationship claim, graph edge, lineage entry, cache record, or run task is created. Persist the
verification page/final URL as independent evidence and link it to the derived domain relationship
alongside the discovery-source observation. Empty, unreachable, redirected-without-ID, and mismatched
pages abstain. Do not apply this rule to paid provider capabilities that already return authoritative
historical tracker-domain records until their contracts expose record-level verification semantics;
for the current generic reverse-search seam, verification is mandatory regardless of free/paid tier.
Use a lightweight root-only verification method so legal-page enrichment is not repeated, and count
each verification as its own provider call with normal rate, concurrency, response, request, page,
timeout, circuit, and telemetry controls.

Tracker verification progress (2026-08-14): root-only exact verification, typed verification
results, per-candidate provider execution/telemetry, dual-evidence claims, and provenance-complete
cache replay are implemented. Placeholder/sample/repeated/all-zero IDs are rejected before pivoting,
and family-specific confidence distinguishes publisher, legacy analytics, tag-manager, and GA4 IDs.
Tests prove matched candidates map and enqueue, mismatches create no relationship or task, each
accepted claim has discovery plus verification evidence, and cache replay retains both. The full
suite has 224 passing tests. A real `example.com` verification for `G-ABCDEF12` returned the final
HTTPS URL and `matched=false`, demonstrating correct production abstention. Positive verified
cross-domain recall remains unmeasured without an authorized pair sharing a tracker.

### Phase 4 — Query planner and cost optimizer

Deliver:

- Free/cache-first deterministic planning.
- Evidence-gap and expected-information-gain model.
- Profiles, preflight plan, manual approvals, cost/call/token/time ceilings.
- Source equivalence and independence graph.
- Actual-versus-estimated usage reporting.

Exit gate: compared with an all-sources baseline, the planner preserves agreed quality while
reducing unnecessary calls and tokens by a measured target set from Phase 3 data.

Phase 4 first increment boundary (2026-08-14): introduce an offline `plan` command and make provider
spend policy explicit. Profiles are `free` (default; never instantiate billable providers) and
`byok` (configured free and paid providers, requiring an explicit per-command `--approve-paid` for
an actual run). An API key alone is configuration, not spending authorization. The preflight plan
must use provider manifests only—zero database, network, model, or billable calls—and list selected,
unavailable, policy-excluded, unconditional, conditional, and approval-gated sources. Report
per-domain logical-call and upstream-request upper bounds separately from whole-run worst cases;
conditional acquisition, reverse-pivot, verification, subdomain, and historical work must be named
rather than hidden inside a single estimate. Preserve the existing crawl-size `--budget` option but
label it as breadth/depth, not monetary budget.

Add hard run ceilings for logical provider calls and billable units at the shared executor seam so
all current and future adapters inherit enforcement, including retries and tracker verification.
Reserve the conservative worst-case billable units before network admission; a rejected call emits
zero-unit `budget_exceeded` telemetry and cannot reach provider code. Defaults: a finite logical-call
ceiling suitable for local runs and zero billable units in `free`; `byok` requires the user to set a
positive ceiling as well as approve the run. Persist configured ceilings/profile in effective config
and include actual-versus-limit usage in reports. This increment does not yet claim information-gain
optimization; source skipping based on measured evidence gaps follows after the safety/preflight
contract is real and tested.

Implementation progress (2026-08-14): the offline manifest-only planner, `free`/`byok` profiles,
explicit paid approval, and separate logical-call and billable-unit ceilings are implemented. The
shared executor reserves retry-inclusive worst-case paid units before provider code can run and
records rejected calls as zero-attempt, zero-unit `budget_exceeded` telemetry. Migration 0009 stores
the effective profile and ceilings on each run; Markdown reporting shows actual usage beside those
limits. Runtime selection falls back to free sources when a configured paid source is disallowed,
so an API key alone cannot silently enable spend. Conservative preflight bounds can exceed the hard
ceiling and are surfaced as warnings; this is expected because the ceiling terminates conditional
work rather than pretending the entire theoretical expansion will fit. Information-gain scheduling
and source-independence optimization remain deliberately unimplemented until evaluated data exists.

Phase 4 second increment boundary (2026-08-14): add a deterministic evidence-gap scheduler at the
point where the initial registration/web evidence has already been collected. It must allocate the
limited pivot slots across distinct downstream jobs (`asset_discovery` via reverse lookup and
`corporate_control` via organization sources), rather than letting several near-duplicate pivots for
one job crowd out the other. Rank within a job using an explicit, versioned utility function based
on evidence confidence, identifier specificity, verification overhead, and expected logical-call
cost. Persist the utility, target gap, and policy version with each pivot decision so reports and
evaluations can explain why a call was selected. Keep this deterministic and abstaining: generic
names and organization values are not sent to noisy reverse search, and unmeasured provider-recall
claims are not embedded as invented probabilities. Add counterfactual tests showing better gap
coverage under the same top-k call budget. Provider-yield learning from evaluation telemetry follows
only after enough labeled cases exist.

Implementation progress (2026-08-14): `pivot-utility-v1` now assigns every eligible pivot to
`asset_discovery` or `corporate_control`, prices tracker verification into its logical-call estimate,
and reserves one slot for each represented gap before filling the remaining top-k slots by utility.
Migration 0010 persists gap, utility, estimated calls, and policy version in `pivot_decisions`; the
Markdown report exposes them. Counterfactual tests prove that a two-slot budget covers both gaps
where the previous confidence-only sort spent both slots on emails. The full suite has 235 passing
tests. A read-only replay over existing real-run pivots selected a nameserver asset route and a legal
organization route within the same three-slot budget, confirming the policy executes on non-fixture
data; its effect on verified recall is still unmeasured and is not claimed.

Phase 4 third increment boundary (2026-08-14): model evidence independence explicitly before it can
affect confidence. Add a versioned source-family catalog for built-in providers and their important
derived sources. Evidence from two adapters backed by the same upstream family counts as one family;
unknown sources remain labelled `unclassified` rather than silently asserted to be independent.
Annotate exported claim evidence with family IDs, family counts, and an independence classification.
Expose provider families in offline preflight steps so redundant cascades are visible. Correct
existing report/reason wording that calls duplicate observations independent without checking
lineage. This increment is descriptive only: it must not raise claim scores until held-out
calibration defines how independent-family counts affect confidence.

Implementation progress (2026-08-14): `source-family-v1` classifies built-in primary, aggregate,
archive, search, registration, and derived sources. Claim exports now annotate each evidence row and
provide a conservative family summary; any unknown lineage makes the independence status
`unclassified`, while RDAP plus legacy WHOIS correctly remains one registration family. Provider
manifests and offline plans expose the same family IDs, and misleading “independent observation”
wording was removed. This is intentionally descriptive and does not alter confidence. The full suite
has 239 passing tests. A real CLI preflight showed both registration adapters under one family while
current web, authoritative DNS, and web search remained distinct. Existing local historical runs did
not contain evidence-backed claims suitable for a non-fixture report replay, so live claim annotation
against real source evidence remains unverified rather than assumed.

Phase 4 fourth increment boundary (2026-08-14): add an offline per-run provider-value report derived
from the immutable claim/evidence ledger and provider telemetry. Report supporting claims, verified
support, sole classified-family support, corroborated support, and supported domain objects by source
family alongside calls, requests, latency, and billable units. Keep causal language out of the
contract: sole-family support is not “incremental lift,” and a paid-vs-free counterfactual requires
matched benchmark runs. Unclassified lineage cannot earn sole-family credit. Expose the report as
`providers value --run-id ID` in text and JSON, with zero network/model/billable calls. This creates
the measurement substrate; planner weights must not learn from it until labeled outcome counts and
minimum-sample safeguards are implemented.

Implementation progress (2026-08-14): `providers value --run-id ID` now produces an offline text or
JSON contribution report from persisted claims and telemetry. It counts family-level supporting,
verified, sole-family, corroborated, and domain-object contributions and lists recorded calls,
requests, latency, and units separately. Unclassified evidence receives no sole-family credit, and
the machine-readable contract explicitly states that attribution is not causal lift. The command
opens only SQLite and remains usable even when provider or cloud-model configuration would prevent a
scan runtime from being constructed. The full suite has 242 passing tests. A real invocation against
the latest stored `stripe.com` run performed zero network/model/billable calls and honestly reported
that the legacy run contained neither claim-ledger contributions nor provider telemetry. Meaningful
provider comparison still requires new provenance-complete matched benchmark runs.

Phase 4 fifth increment boundary (2026-08-14): implement an offline matched graph comparison using
one versioned evaluation case. Require identical normalized roots and reject conflicting crawl/model
policy fields when both exports provide them; provider profile may differ intentionally. Compare
candidate-only and lost predictions, new/lost true positives, new/resolved known false positives,
unlabeled discoveries, metric deltas, and provider call/unit deltas. Produce a conservative verdict
and an explicit `eligible_for_planner_learning` gate requiring minimum labeled outcomes and positives.
One case or synthetic fixtures may validate mechanics but must never train provider priority. Expose
this as `providers compare --baseline FREE.graph.json --candidate BYOK.graph.json --case CASE.json`,
performing zero database/network/model/billable calls.

Implementation progress (2026-08-14): the matched comparison command now reports candidate-only and
removed predictions, labeled gains/regressions, metric and usage deltas, incremental calls/units per
new labeled true positive, and a conservative verdict. It rejects conflicting exported crawl or
model policy fields and lists any fields that could not be verified. Migration 0011 makes new runs
persist run mode, resolved model, routing-policy version, and cache mode so future graph pairs can be
checked rather than trusted. Learning is denied below 20 labels/10 positives, when candidate-only
results remain unlabeled, or when policy fields are missing. The full suite has 247 passing tests.
A real CLI execution on the bundled synthetic graph pair made zero external calls, correctly found
no quality change, and refused planner learning because the two-label fixture and legacy graph policy
metadata are insufficient. No real free/BYOK lift is claimed until matched authorized runs exist.

Phase 4 sixth increment boundary (2026-08-14): aggregate matched comparisons through a versioned,
path-portable benchmark manifest. Resolve graph/case paths relative to the manifest, reject duplicate
case IDs and duplicate roots, and require one consistent set of matched policy values across every
pair. Compute micro precision/recall/F1 from pooled labeled outcomes, aggregate candidate-only/lost
results by `root/domain`, sum usage deltas, and calculate cost per net new true positive. Planner
learning requires the corpus-wide label thresholds, no unlabeled candidate additions, no policy gaps,
and no case-level degradation. Emit per-case details plus corpus totals through an offline
`providers benchmark --manifest FILE` command. Do not average per-case percentages, which would
overweight small organizations.

Implementation progress (2026-08-14): `providers benchmark --manifest FILE` now loads relative
case/baseline/candidate paths, rejects duplicate case IDs and roots, enforces consistent exported
policies across pairs, and aggregates labeled outcomes as micro precision/recall/F1. It scopes result
sets by root, sums usage, computes calls and units per net new true positive, and blocks learning for
small/incomplete/unmatched corpora or any degraded case. A checked-in synthetic manifest documents
the schema. The full suite has 250 passing tests. A real offline CLI execution on that manifest
reported unchanged metrics and correctly refused learning because it has only two labels and legacy
graphs lack policy snapshots. The benchmark machinery is ready; the required real labeled corpus and
matched free/BYOK exports do not yet exist, so Phase 4’s measured-quality exit gate remains open.

### Phase 5 — LLM gateway and calibrated model routing

Phase 5 first increment boundary (2026-08-14): close the unbudgeted cloud-model path before adding
model recommendations. Local Ollama remains the default. A cloud model requires explicit per-run
`--approve-cloud` plus a positive `--max-cloud-tokens`; an API key and `allow_cloud` configuration
alone are not spending authority. Add hard run-wide ceilings for model calls, conservatively
estimated input tokens, reserved output tokens, and total cloud tokens at a shared typed gateway.
Reserve budgets before invoking LiteLLM and fail closed without reaching the SDK. Keep deterministic
extraction available when the model budget is exhausted. Persist the model policy and ceilings on
the run and expose them in config/reporting. This increment uses a conservative UTF-8-byte upper
bound rather than pretending exact tokenizer parity across providers. Do not spend the available
OpenAI test balance until the gateway, telemetry, and an explicit tiny live-test cap are in place.

Implementation progress (2026-08-14): cloud egress now defaults off. A cloud model is rejected before
runtime construction unless administrative `allow_cloud`, per-run `--approve-cloud`, and a positive
cloud-token ceiling are all present. The shared `ModelBudget` reserves a tokenizer-independent UTF-8
input upper bound plus requested output before LiteLLM, with hard call/input/output/cloud ceilings;
rejection returns deterministic results and never reaches the SDK. Migration 0012 persists approval
and model ceilings, configuration and reports expose them, and new matched benchmarks require these
fields to agree. The full suite has 256 passing tests (with one pre-existing Windows asyncio transport
cleanup warning). Real CLI checks rejected both unapproved and administratively disabled cloud use.
No OpenAI request or balance was consumed. Provider-reported actual token/cost telemetry remains the
next gateway increment, so reserved upper bounds must not yet be described as billed usage.

Phase 5 second increment boundary (2026-08-14): persist one model-call telemetry row for every SDK
admission outcome. Store run/domain/model/task/policy correlation, local-vs-cloud, status, conservative
input/output/cloud reservations, provider-reported prompt/completion/total tokens when present,
provider-reported response cost when present, latency, and bounded error metadata. Never estimate
billed dollars from a hard-coded pricing table; unknown cost remains null. Export and report actual
usage separately from reservations. Budget rejection records zero actual tokens/cost and cannot reach
LiteLLM. Responses without usage metadata remain successful with unknown actual usage, not zero.

Implementation progress (2026-08-14): migration 0013 adds durable `model_calls` telemetry and DB
integrity checks now require it. The model client records success, error, and pre-SDK budget rejection
with run/domain/model/task/policy correlation, conservative reservations, latency, bounded errors,
provider-reported actual token fields, and provider-reported response cost when LiteLLM exposes it.
Graph exports and Markdown reports distinguish actual, reserved, and unknown values. The full suite
has 259 passing tests. An installed-code run through real SQLite rejected a `gpt-5-mini` call at a
zero cloud-token ceiling, returned deterministic empty model output, and persisted one
`budget_exceeded` row with zero reservation, null actual tokens, and null cost; LiteLLM was never
reached and no balance was consumed. Cross-process/resume-safe model admission remains incomplete;
the in-memory reservation counter must not yet be treated as a durable lifetime quota.

Phase 5 third increment boundary (2026-08-14): make model admission durable across concurrent
processes and resume. Before SDK execution, atomically read the immutable run ceilings, sum existing
reservations, and insert the next reservation in one SQLite write transaction. A rejected reservation
must leave no row. Reservations are never refunded: a crash between admission and telemetry remains
conservatively charged because the upstream outcome is ambiguous. Export reservation totals beside
limits so crash-only consumption is visible rather than lost from model-call telemetry.

Implementation progress (2026-08-14): migration 0014 adds append-only model budget reservations.
The real LLM client uses repository-backed atomic admission whenever a run ID exists and falls back
to its in-memory budget only for standalone/test calls. Reports show durable calls/input/output/cloud
reservations against run limits. Tests with independent SQLite connections prove that the second
process cannot cross a one-call ceiling, a resumed process sees a crash-only reservation, and a
rejected request rolls back without consuming quota. Cross-process/resume-safe model admission is
therefore implemented. The full suite has 262 passing tests. An installed-code check with two live
SQLite connections admitted the first reservation, rejected the second at the shared one-call
ceiling, and reported identical durable usage from the second connection. Provider-reported quota
reconciliation and idempotent ambiguous-call recovery remain separate future work.

Phase 5 fourth increment boundary (2026-08-14): make exact model requests idempotent without storing
raw prompts or prose. Derive a SHA-256 request key from run, normalized domain, task, policy, model,
and the exact compact prompt. Enforce one reservation per key. Persist only bounded normalized pivot
objects on successful calls. A later identical request replays those pivots with no reservation or
SDK call. If a reservation exists without a successful normalized result, classify it as ambiguous
and suppress retry; do not gamble paid tokens. Changed evidence produces a different key and remains
eligible within the run budget. Cache lookup and reservation must remain race-safe across processes.

Implementation progress (2026-08-14): migration 0015 adds request keys to reservations and model
calls plus bounded normalized result storage. The key hashes the run/domain/task/policy/model/exact
compact prompt; neither raw prompts nor raw model prose are persisted. Successful results replay
without a reservation or SDK call. A reservation lacking successful result telemetry causes the
identical request to be rejected as `ModelDuplicateReservationError`, recorded as a zero-usage
budget rejection, while changed evidence receives a different key. The progress display now counts
actual SDK calls rather than every relationship pass. The full suite has 265 passing tests, including
successful replay, timeout ambiguity suppression, and changed-evidence admission. No live cloud call
was made, so behavior against a provider’s own idempotency or quota APIs remains unverified.

Next implementation boundary (Phase 5, increment 5): put a deterministic, versioned egress policy
in front of every model call. Cloud models receive an allowlisted evidence projection that removes
personal WHOIS name/email/phone fields and all unrestricted/raw fields while retaining bounded
organization, lifecycle, nameserver, hostname, redirect, tracker, and current-web relationship
signals. Local Ollama receives a fuller but still schema-limited and size-bounded projection. All
strings, lists, and nesting are normalized and capped; webpage-derived strings remain quoted JSON
data and the system prompt must explicitly treat all supplied evidence as untrusted and never follow
instructions embedded in it. Model telemetry records the egress-policy version, never the prompt or
evidence payload. Tests must prove cloud messages omit sentinel PII, inputs are not mutated, bounds
hold, prompt-injection text cannot become an instruction role, and the recorded policy version is
queryable. Verification uses captured/fake SDK calls only; no live cloud call or account balance.

Implementation progress (2026-08-14): the fifth Phase 5 increment is implemented. The model gateway
now applies `cloud-redacted-v1` or `local-structured-v1` before prompt construction and request-key
generation. The cloud projection drops registrant name/email/phone and every non-allowlisted/raw
field; both projections cap strings at 1,000 characters and lists at 250 entries without mutating
source evidence. Prompt instructions and the user-message label treat evidence as untrusted JSON.
Migration 0016 records `egress_policy_version` on every model-call telemetry row without storing the
prompt or evidence. A captured fake cloud SDK invocation verified that sentinel contact data was
absent, corporate data remained, and injection-like webpage text stayed inside one user JSON message
under an explicit non-execution instruction. The full suite has 268 passing tests and source/test
bytecode compilation succeeds. No live cloud call was made, so provider-side handling remains
unverified.

Phase 5 sixth increment boundary (2026-08-14): replace best-effort model-output salvage with a
versioned strict response contract. Send a JSON Schema response format through the shared gateway;
require an explicit `abstain` decision, bounded reason, and at most 20 typed pivots with no unknown
fields. An abstention must contain no pivots, while a non-abstention must contain at least one. Parse
only the complete response as JSON—never extract objects from prose or Markdown—and reject the
entire payload if any item violates the contract. Keep semantic pivot/noise validation after schema
validation. Advance the prompt/model policy version so old idempotency entries cannot replay under
the new contract. Persist a separate output disposition (`accepted`, `abstained`, or `invalid`) in
model telemetry, without storing raw output. Tests must cover schema transmission, strict rejection,
explicit abstention, output bounds, telemetry, and successful normalized replay. Use captured/fake
SDK calls only; do not spend cloud balance in this increment.

Implementation progress (2026-08-14): the sixth Phase 5 increment is implemented as model policy
`relationship-pivot-v2`. The gateway sends the strict `reconrelate_relationship_pivots_v2` JSON
Schema, validates the complete response with strict typed models, caps output at 20 pivots, requires
a consistent explicit abstention decision, and no longer extracts JSON from prose or Markdown.
Migration 0017 adds `output_disposition`; CLI Markdown model-usage output now exposes disposition and
egress policy. Accepted results and deliberate abstentions can replay, while invalid output stores no
normalized result and an identical ambiguous retry is suppressed by its reservation. The full suite
has 273 passing tests and source/test bytecode compilation succeeds. A real installed-code call to a
local Ollama `qwen2.5:7b-instruct` model accepted the schema and returned one typed organization pivot
with `status=success` and `output_disposition=accepted`; an initial cold-model attempt exceeded a
deliberately shortened 90-second timeout, while the warm call completed in about 18 seconds under the
normal 120-second timeout. No cloud request or paid balance was used. Cloud-provider JSON Schema
compatibility remains unverified until the separately authorized capped live check.

Phase 5 seventh increment boundary (2026-08-14): make local/cloud model setup diagnosable from the
CLI before attempting a recon run, and establish the release-versioned recommendation catalog
without inventing quality claims. Add `models list` for catalog provenance and `models doctor` for
the effective model, local Ollama reachability/installation, cloud administrative gate and credential
presence, structured-output policy, and timeout coherence. The doctor may query the configured
Ollama tags endpoint but must never call a model or cloud API, expose a credential value, mutate
configuration, or label an unevaluated model as recommended. Catalog entries distinguish transport
compatibility evidence from held-out quality eligibility; automatic selection remains disabled until
the evaluation gate is met. Repair default timeout coherence using the observed cold-load behavior:
the model timeout must be at least 120 seconds and the containing per-domain timeout must exceed it.
Tests must cover local ready/missing/unreachable states, cloud gating without egress, catalog honesty,
CLI JSON/text output, and coherent defaults. Verify with the real installed CLI against local Ollama.

Implementation progress (2026-08-14): the seventh Phase 5 increment is implemented. The new
`models list` command exposes catalog `2026.08.14-v1`, compatibility and quality status separately,
and an explicit null automatic recommendation because no model has passed the held-out quality/cost
gate. `models doctor` checks the effective model and response policy, performs only a bounded Ollama
tags lookup for local configurations, verifies exact model installation, reports cloud gate/key
presence without revealing values or contacting the provider, and rejects incoherent timeouts.
Observed local cold-start behavior corrected defaults from 60/90 seconds to 120 seconds for the model
inside a 180-second domain boundary; `.env.example` now agrees with runtime defaults. The full suite
has 279 passing tests and source/test bytecode compilation succeeds. A real installed CLI doctor
against `http://localhost:11434` found `qwen2.5:7b-instruct`, reported the strict v2 contract and
coherent 120/180-second boundaries, and exited ready; the catalog CLI separately reported that its
quality remains unevaluated. No generation, cloud call, or paid balance was used by the doctor.

Phase 5 eighth increment boundary (2026-08-14): add a matched, provenance-required model benchmark
that compares the exact deterministic pivot baseline with one configured model on identical saved
evidence. Define versioned case and manifest schemas with expected normalized pivots or an expected
abstention, source references for every label, and no live evidence collection during evaluation.
Add `models benchmark --manifest ... --model ...` with the same cloud administrative/per-run approval
and hard call/input/output/cloud-token ceilings as recon runs. Report per-case and aggregate precision,
recall, F1, abstention correctness, invalid/error rate, latency, provider-reported tokens/cost, and
baseline-to-model deltas. Recommendation eligibility must fail closed unless the corpus has enough
positive and abstention cases, every case completes validly, model precision does not regress, recall
improves materially, and cloud cost is known and within its declared ceiling. Synthetic examples may
validate machinery but can never promote a catalog entry. Tests use a fake model gateway; reality
verification uses the installed local model on a checked-in non-eligible example and must not claim
quality from that example. No cloud call is authorized in this increment.

Implementation progress (2026-08-14): the eighth Phase 5 increment is implemented. Versioned model
cases now allowlist the exact structured evidence schema, reject raw/unknown fields, require source
references for every expected pivot, support explicitly labeled equivalent values, and distinguish
synthetic from held-out data. `models benchmark` applies the deterministic scorer and model gateway
to the same saved evidence, scores their union as the assisted product path, and reports case and
aggregate precision/recall/F1, deltas, abstention correctness, invalid outcomes, latency, actual
tokens, and provider-reported cost. Cloud execution is blocked without the administrative gate,
per-command approval, and positive hard cloud-token budget. Eligibility requires a wholly held-out
corpus of at least 20 cases (10 positive and 5 abstention), valid completion, non-regressing precision,
at least 0.05 recall lift, and known cloud cost. The full suite has 284 passing tests and source/test
bytecode compilation succeeds. A real installed CLI benchmark ran the one-case synthetic manifest
through local `qwen2.5:7b-instruct` in about 22.5 seconds and 716 reported tokens. Its added org pivot
did not match the provenance label, producing precision/recall 0/0 and six explicit ineligibility
reasons. This negative result is retained: it proves the gate rejects a compatible model rather than
tuning the smoke fixture or promoting it. No cloud call or paid balance was used. A sufficiently
large independently labeled held-out corpus is still required to open Phase 5's recommendation gate.

Phase 5 ninth increment boundary (2026-08-14): activate the existing but currently inert
`fast_model` setting as an explicit economical-first route. Deterministic extraction remains first
and skips all models when strong. When escalation is needed and a distinct fast model is configured,
call it once through the same egress, schema, idempotency, telemetry, and durable budget gateway.
Accept its result only when the complete output is valid and at least one semantically valid pivot
scores at or above a versioned 0.75 route threshold. Otherwise call the configured primary model as
the stronger route; discard low-confidence/invalid fast output rather than merging noise. A fast-call
budget rejection or ambiguous duplicate must fail closed without trying a second model. Persist both
models and the routing-policy version on the run, and label telemetry tasks as fast versus strong.
Cloud authorization must inspect both primary and fast models before runtime construction so a cloud
fast model cannot bypass CLI approval. Tests must prove short-circuiting, escalation, discard behavior,
two-call budget accounting, cache separation, and mixed local/cloud security. Reality verification
uses two installed local models only; no cloud call is authorized.

Implementation progress (2026-08-14): the ninth Phase 5 increment is implemented as routing policy
`economical-first-v1`. A distinct fast model now receives the first ambiguity call; only a complete
accepted response with a semantically valid pivot at or above 0.75 short-circuits. Abstention,
invalid/error output, or low confidence escalates to the primary model, with insufficient fast output
discarded. Budget/duplicate rejection stops without a second attempt. Each route has a distinct task
and request key while sharing durable budgets, egress, schema validation, replay, and telemetry.
Migration 0018 persists `fast_model` and `model_routing_policy`; matched provider comparisons now
require them, reports expose them, and the model doctor validates both local/cloud models. CLI and
factory authorization inspect both models, closing the previous cloud-fast bypass. The resolver now
treats unrecognized namespaced tags as local Ollama models while preserving an explicit allowlist of
known cloud-provider prefixes. The full suite has 294 passing tests and compilation succeeds. A real
installed-code call used local Qwen as fast and a distinct local Llama as primary: Qwen returned an
accepted organization pivot at exactly 0.75, so telemetry recorded only
`relationship_pivot_fast`, one SDK call, and one reservation; the primary never loaded. A real CLI
doctor then recognized the namespaced Llama tag and both installations as local and ready. No cloud
call or paid balance was used. Routing quality and its 0.75 threshold remain ineligible for default
activation until the held-out model corpus measures precision, recall lift, and cost.

Phase 5 tenth increment boundary (2026-08-14): add a true pre-call cloud dollar envelope before the
first paid OpenAI compatibility test. Token ceilings remain necessary but are not a spending unit.
Introduce a dated, release-versioned price catalog using official provider documentation, store rates
per exact model/alias, and compute a conservative worst-case reservation from the existing UTF-8-byte
input upper bound plus maximum output tokens. Round upward to integer microdollars and reserve them
atomically with calls/tokens across processes; reservations are never refunded. Every cloud run and
cloud model benchmark must require a positive `--max-cloud-cost-usd` in addition to approval and token
ceilings. Unknown models or a price catalog older than 90 days fail before SDK execution rather than
assuming a rate. Persist the catalog version, ceiling, reservation, and usage totals; distinguish the
reserved envelope from provider-reported actual cost. Seed only officially verified OpenAI entries—
currently GPT-5 mini ($0.25/M input, $2/M output) and GPT-5.6 Luna ($0.20/M input, $1.20/M output),
verified 2026-08-14—and do not infer quality from price. Tests must cover rounding, stale/unknown
rejection, concurrent durable admission, CLI requirements, reporting, and local zero-cost behavior.
The current process has no `OPENAI_API_KEY`, so this increment must stop after non-network reality
checks unless a key becomes available; it must not search files or print credentials.

Implementation progress (2026-08-14): the tenth Phase 5 increment is implemented. Catalog
`openai-2026.08.14-v1` records official GPT-5 mini and GPT-5.6 Luna text-token rates and source URLs;
the user-facing model catalog is now `2026.08.14-v2` and exposes those prices while leaving both
models unevaluated. Cloud admission prices the conservative input-byte token bound plus all 512
reserved output tokens, rounds upward to integer microdollars, and rejects unknown or more-than-
90-day-old rates. Migration 0019 adds immutable run dollar ceilings/catalog versions, reservation
microdollars, and model-call price telemetry. In-memory and cross-process SQLite admission enforce
the same cumulative ceiling without refunds. Run and benchmark CLIs require a positive
`--max-cloud-cost-usd`; reports distinguish reserved envelopes from provider-reported actual cost,
and `models doctor` includes price-envelope readiness. The final full suite has 299 passing tests and
source/test bytecode compilation succeeds. A real installed CLI run
attempt with cloud enabled, explicit approval, and 2,000 cloud tokens but a zero-dollar ceiling exited
2 before runtime/provider execution; `models list` displayed both official rate pairs and no automatic
recommendation. `OPENAI_API_KEY` was absent from the process, so no paid compatibility call or balance
check was possible and no credential files were searched. Official pricing is documented in
`docs/MODEL_GATEWAY.md`; external price changes after the verification date remain a deliberate
fail-closed update requirement.

Deliver:

- Typed task gateway, structured outputs, prompt/model/config versioning, abstention.
- Cloud/local egress controls and WHOIS redaction.
- Deterministic-first, economical-model, and stronger-escalation routes.
- Model evaluation harness and release-versioned recommendation catalog.
- Test OpenAI using a strict small dollar cap; do not place the key in files or logs.

Exit gate: LLM-assisted mode produces a statistically meaningful improvement over deterministic
mode on held-out ambiguous cases, within the declared cost, without reducing claim precision.

### Phase 6 — BYOK paid enrichment

Phase 6 first increment boundary (2026-08-14): harden the existing Whoxy reverse-WHOIS adapter to
the provider's current official contract before adding another vendor. Keep privacy-preserving
`mode=micro`: one request/page, up to 2,500 historical domain-only rows, no registrant contact fields.
Validate status, API operation, pagination metadata, result-array shape, row objects, official row
ceiling, domain normalization, deduplication, and caller result cap. Missing keys must raise typed auth
failure rather than impersonating a successful empty search; unsupported pivots and zero requested
results remain zero-call empties. Map HTTP auth/rate/service failures without ever including the
query-string key, redact bounded API error reasons, and never log email/name/org pivot values. Treat
only explicit no-record status reasons as a valid empty result; unknown status-zero responses are
errors. Document Whoxy's one-credit successful-page billing and conservative ReconRelate reservation,
plus the provider's historical-record semantics and redistribution/licensing caveat. Add official
success/error fixtures and tests for malformed/oversized responses, secret-safe errors, zero-call
paths, normalization, and manifest bounds. A live paid lookup requires a user key and separate
explicit approval; with no key present, reality verification is limited to doctor/fail-closed paths.

Implementation progress (2026-08-14): the first Phase 6 increment is implemented against Whoxy's
official reverse-WHOIS page, micro JSON sample, error sample, and current terms. The adapter uses one
domain-only micro request/page, enforces 1 MiB and 2,500-row limits, validates status/operation/
pagination/result shape, normalizes and deduplicates historical domain rows, and caps output at the
caller's request. Missing credentials are typed auth failures; only explicit no-record reasons are
empty success. HTTP auth/rate/service errors and bounded API reasons cannot expose the query-string
key, and logs contain pivot type but never email/name/org values. Local oversized input is a typed
non-retryable error that does not damage the provider circuit. Registry defaults are two concurrent,
20/minute, one request/page, and a conservative `successful result page` billing unit. Officially
sourced fixtures cover success and invalid key; malformed, oversized, quota, secret-redaction, and
zero-call paths are tested. `docs/WHOXY.md` records BYOK commands, historical-candidate semantics,
conservative accounting, and source/terms/redistribution responsibilities. The final full suite has
317 passing tests and compilation succeeds. A real offline `providers doctor --json` made zero
network/billable calls, reported Whoxy's exact limits and `configuration_missing`, and confirmed
`WHOXY_API_KEY` is absent. Therefore contract behavior is verified but live Whoxy transport, account
credit reconciliation, and unique held-out recall remain unverified; the adapter is not yet entitled
to Phase 6's quality exit gate.

Phase 6 second increment boundary (2026-08-14): add explicit provider-account quota reconciliation
without turning diagnostics into hidden network or paid activity. Define a typed quota snapshot and
implement Whoxy's documented `account=balance` schema, returning only reverse-WHOIS credits and
discarding other account fields. Add `providers balance --provider whoxy --approve-paid
--max-billable-units N`; the command must require an available key, explicit approval, and at least
one conservatively reserved unit. Because Whoxy documents the balance endpoint but not whether it
consumes a credit, label billing impact `unknown`, use exactly one request/page, disable retries,
and run through normal response/timeout/error/secret controls. Do not persist account balance or key,
and keep ordinary `providers doctor` strictly offline. Missing approval/key/budget must fail before
network. Tests use the official balance schema plus malformed/auth/quota/CLI zero-call paths. With no
key present, reality verification is the installed CLI's fail-closed path only; no live account call
is authorized.

Implementation progress (2026-08-14): the second Phase 6 increment is implemented. A typed
`ProviderQuotaSnapshot` now represents authoritative provider quotas without retaining the rest of
the account payload. Whoxy's official balance fixture is strictly validated: status must be integer
zero/one and `reverse_whois_balance` must be a non-negative JSON integer; auth, quota, malformed,
and unknown provider failures remain typed and secret-safe. The explicit CLI command requires
`--provider whoxy`, `--approve-paid`, and `--max-billable-units >= 1` before constructing a provider.
It executes through `ProviderExecutor` with one call, one request/page, one conservatively reserved
unit, zero retries, normal deadlines/response bounds, and `billing_effect=unknown`; the account
payload is not persisted. Routine provider diagnostics remain offline. Official-fixture, malformed,
error, approval, budget, missing-key, and bounded-success tests are included. The full suite has 331
passing tests and compilation succeeds. Real installed-CLI checks report doctor totals of zero
network and zero billable calls, then reject the explicit balance command with exit 2 before network
because `WHOXY_API_KEY` is absent. No paid account call was made, so live balance transport remains
unverified pending user-supplied credentials and approval.

Phase 6 third increment boundary (2026-08-14): make provider data-use restrictions executable
rather than prose-only. Add a validated, versioned provider policy declaring raw retention,
cross-run cache eligibility, and export scope; expose it through provider diagnostics and attach it
to instantiated adapters. Preserve free-provider behavior through an explicit open-normalized
default. Whoxy is conservative pending a provider-specific legal review: retain no raw response
(hash only), allow normalized evidence inside the originating run, deny cross-run cache replay, and
export only derived claims plus a restricted evidence reference—not the underlying Whoxy
observation fields. Enforce the policy at cache and JSON-export boundaries, keep old database rows
readable through explicit legacy defaults, and ensure current-run discovery remains usable. Add
tests proving restricted records cannot enter shared cache or leak into graph artifacts, while free
records still round-trip. Diagnostics must surface the effective policy so adding a future paid
adapter cannot hide its storage behavior. This is a technical fail-closed default, not a legal
conclusion; final retention periods and provider-specific redistribution approval remain release
authority decisions.

Implementation progress (2026-08-14): the third Phase 6 increment is implemented as
`provider-data-use-v1`. Every registry entry exposes raw retention, normalized retention, shared
cache eligibility, export scope, terms URL, and review date; paid registrations fail immediately
unless they declare a policy. Free built-ins explicitly retain the existing hash-only raw handling,
project-normalized cache, and normalized export behavior. Whoxy is run-local, cross-run-cache
denied, hash-only for raw material, and `derived_only` for portable exports. The orchestrator
disables both shared-cache reads and writes for a run containing any non-cacheable active provider,
so a free cache cannot mask paid enrichment and restricted results cannot be replayed. Observation
rows persist their governing policy through migration 0020. JSON tree/report/export paths all apply
one fail-closed export filter: restricted observations are omitted while derived claims retain only
bounded attribution, scoring, time, and policy references. Human/JSON provider diagnostics expose
the effective policy, and `docs/PROVIDER_DATA_POLICY.md` documents the matrix and legal boundary.
Denial, compatibility, migration, free round-trip, and paid-registration tests are included. The
full suite has 340 passing tests and compilation succeeds. A real free CLI run against example.com
completed with one domain, five edges, zero model calls, and a hard 12-provider-call ceiling; its
real exported artifact reports policy enforcement, 14 normalized observations, four claims, and no
restricted omissions. Real offline doctor output reports zero network/billable calls and Whoxy's
`cross_run_cache=false`, `export_scope=derived_only`, `raw_retention=hash_only`. Live Whoxy export
behavior remains contract-tested rather than account-tested because no key is present and no paid
call was authorized.

Deliver:

- Complete Whoxy adapter and contract/live tests.
- Add DNSlytics and/or WhoisXML based on evaluation needs and accessible test credentials.
- Later add SecurityTrails/DomainTools only with licensed test access.
- Licensing-aware cache/export rules and provider-specific quota reconciliation.
- Marginal value report: unique verified results and cost per provider.

Exit gate: disabling all paid providers still yields the production free experience; enabling a
provider produces a measured, explainable improvement and cannot exceed the user's hard budget.

### Phase 7 — Production hardening and public release

Deliver:

- Performance profiling and realistic scale/failure tests.
- Backup/restore/rollback rehearsal and operational runbooks.
- Stable JSON schema, exit codes, compatibility/deprecation policy, and provider author guide.
- Privacy, retention, terms matrix, threat model, responsible-use policy, security contact.
- Reproducible signed package/release artifacts and public benchmark methodology.

Exit gate: all release gates in section 12.3 pass using real authorized runs. Anything not verified
is documented as an explicit limitation, not described as production-ready.

## 14. Work sequencing and change boundaries

- Implement phases in order; within Phase 3, add one source at a time and measure it.
- Keep the CLI compatible while internals migrate; add deprecation warnings before removals.
- Do not add a hosted service, web UI, graph database, or dynamic third-party code plugins during
  this track.
- Do not implement a paid adapter without contract fixtures and a way to run an opt-in live check.
- Do not tune weights and the evaluation labels in the same change.
- Update this plan whenever scope, architecture, or phase gates change.

## 15. Self-interrogation outcome

Domains considered: requirements; backend orchestration; data and migrations; provider APIs;
security; local infrastructure and CI; testing; observability; AI/LLM behavior; CLI UX; cost and
performance; privacy/licensing; maintenance.

Decisions that shaped the plan:

- Local CLI/library first, not SaaS.
- Modular monolith and SQLite remain appropriate until measurements disprove them.
- Observation ledger is the source of truth; claims and graph are derived.
- Deterministic planner controls tools and spend; LLM handles ambiguity only.
- Free mode is a supported product profile, not a degraded demo.
- Paid providers are BYOK and evidence-gated.
- Confidence must be calibrated from evaluation data and expose abstention.
- Production readiness requires real authorized runs, restore/rollback, provider failure tests,
  and cost enforcement—not only unit tests.

Assumptions to flag if wrong:

- Initial users are technical single-user CLI users running on their own machines.
- The project remains passive by default and does not perform exploitation.
- No central service stores user scans or API keys.
- Contributors can add adapters, but arbitrary third-party Python code is not loaded dynamically.
- The initial owner accepts a local-tool reliability target rather than 24/7 service uptime.

Authority decisions deferred until their implementation phase:

- Final default retention periods for WHOIS/raw web evidence.
- Exact acceptable spend ceiling and provider priority for `byok-auto`.
- Whether an active probing profile will ever be part of ReconRelate.
- The owner/security-contact identity used in public release documents.

Top risks:

- False ownership claims cause users to scan out-of-scope assets.
- More sources increase correlated noise rather than independent evidence.
- Provider terms prohibit desired caching or redistribution.
- LLM fluency is mistaken for evidence and damages precision.
- Retries/resume duplicate paid calls or create surprise bills.
- Free bulk datasets introduce impractical storage and maintenance costs.
- A scoring policy overfits the public evaluation organizations.

The cheapest falsification is Phase 0 followed by Phase 1: if a provenance-complete baseline and
ground-truth corpus cannot distinguish verified ownership from candidate relationships, adding
providers or stronger models will only make the graph larger, not better.
