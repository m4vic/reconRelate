# Model gateway and cloud approval

ReconRelate defaults to local Ollama and disables cloud egress. Setting an API key or a cloud model
does not authorize a request. A cloud run requires all of the following:

1. `RECONRELATE_LLM_ALLOW_CLOUD=true` (or `reconrelate config set allow_cloud true`).
2. A cloud model and its provider key.
3. Per-run `--approve-cloud`.
4. A positive `--max-cloud-tokens` hard ceiling.
5. A positive `--max-cloud-cost-usd` pre-call cost ceiling.

```powershell
reconrelate run example.com `
  --model gpt-5-mini `
  --approve-cloud `
  --max-cloud-tokens 10000 `
  --max-cloud-cost-usd 0.05 `
  --max-model-calls 5
```

The gateway reserves one model call, a conservative UTF-8-byte upper bound for input tokens, the
requested maximum output tokens, and—when cloud-backed—the combined cloud-token allowance before
calling LiteLLM. Crossing any run-wide ceiling fails closed before SDK execution. The byte bound is
intentionally conservative and portable across model tokenizers; it is not presented as actual
billed usage.

Cloud calls also reserve a worst-case cost envelope before execution. ReconRelate multiplies the
conservative input-token upper bound and full output ceiling by a dated, model-specific price catalog,
rounds upward to integer microdollars, and reserves that amount atomically. Unknown models and a
catalog older than 90 days are rejected before the SDK. Reservations are not billed-cost claims;
provider-reported actual cost remains separate telemetry.

Catalog `openai-2026.08.14-v1` uses official OpenAI documentation for
[GPT-5 mini](https://developers.openai.com/api/docs/models/gpt-5-mini) ($0.25/M input,
$2/M output) and [GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna)
($0.20/M input, $1.20/M output), verified 2026-08-14. Lower price makes Luna a candidate for
evaluation, not a quality recommendation.

Model calls remain deterministic-first: when a sufficiently strong structured pivot exists, the
relationship engine skips the model. Budget exhaustion returns the deterministic candidates rather
than fabricating model output.

## Evidence egress

Every model request passes through a versioned, schema-allowlisted evidence projection. Cloud
models never receive registrant name, email, or phone fields, unrestricted provider payloads, or
raw WHOIS data. They may receive bounded organization, lifecycle, nameserver, hostname, tracker,
redirect, and webpage-derived relationship fields. Local Ollama may receive the structured contact
fields, but still never receives unrestricted/raw fields and uses the same string and list bounds.

Webpage text and every other evidence field are untrusted JSON data. The model instructions forbid
following commands, links, or role changes embedded in evidence. Telemetry stores the egress-policy
version for auditability; it does not store the prompt or evidence payload.

## Strict model output

Relationship calls send a versioned strict JSON Schema response format. A response must explicitly
choose either an abstention with a reason and no pivots, or a non-abstention containing one to 20
typed pivots. Unknown fields, invalid items, inconsistent decisions, Markdown fences, and prose
around JSON invalidate the complete output; ReconRelate does not salvage a plausible-looking subset.
Semantic noise filtering still runs after schema validation.

Telemetry records the output disposition as `accepted`, `abstained`, or `invalid`. Raw model prose
is not persisted. Valid normalized results—including deliberate abstentions—remain replayable;
invalid output is not cached and its already-consumed reservation prevents an ambiguous paid retry.

Every admitted, rejected, or failed escalation writes durable telemetry containing the model, task,
policy version, domain/run correlation, reservation, latency, and bounded error metadata. When
LiteLLM exposes provider-reported prompt/completion/total usage or response cost, those values are
stored separately. Missing provider usage or cost remains `unknown`; ReconRelate does not turn it
into zero or calculate dollars from an embedded price table.

Before each SDK call, the gateway atomically inserts a reservation in SQLite. Other ReconRelate
processes and resumed runs therefore see the same lifetime run quota. Reservations are deliberately
not refunded: if a process crashes after admission but before telemetry, the uncertain call remains
charged against the safety ceiling.

Model recommendations, provider quota reconciliation, and calibrated routing remain subsequent
Phase 5 work.

## Setup diagnostics and catalog

Run `reconrelate models doctor` before a model-assisted scan. For local models it performs a bounded
Ollama tags request and verifies that the configured model is installed; for cloud models it checks
the administrative gate and credential presence without contacting the provider or revealing the
key. It also reports the strict-output policy and whether the model timeout fits inside the domain
deadline. `--json` provides the same result for scripts and returns a nonzero exit code when setup is
not ready.

`reconrelate models list` exposes a release-versioned catalog. Compatibility and held-out quality
are separate fields. A transport-compatible model is not automatically recommended until a matched
evaluation demonstrates quality and cost eligibility; the current catalog intentionally has no
automatic recommendation.

## Matched model benchmark

`reconrelate models benchmark --manifest <path> --model <model>` compares deterministic extraction
with deterministic-plus-model extraction using identical saved evidence. Versioned cases require
provenance references for every expected pivot and explicitly label abstention cases. Reports include
precision, recall, F1, recall lift, abstention accuracy, invalid/error outputs, latency, actual tokens,
and provider-reported cost when available.

Recommendation eligibility fails closed for synthetic or undersized corpora, invalid calls,
precision regression, insufficient recall lift, inadequate abstention coverage, or unknown cloud
cost. The example manifest is intentionally synthetic and validates only the machinery. A cloud
benchmark uses the normal administrative gate, per-command approval, and hard token ceilings.

## Economical-first routing

Set `FAST_LLM_MODEL` or pass `--fast-model` to enable routing policy `economical-first-v1`. Strong
deterministic evidence still skips models entirely. For ambiguous evidence, the fast model runs first
through the same strict schema, egress controls, cache, durable reservation, and telemetry. A valid
fast response short-circuits only when it contains a semantically valid pivot scoring at least 0.75.
Abstention, invalid output, call errors, or lower confidence escalate to the primary model, and the
insufficient fast output is discarded rather than merged. A budget rejection or ambiguous duplicate
fails closed without attempting another model.

Both models are persisted in the run snapshot and telemetry distinguishes `relationship_pivot_fast`
from `relationship_pivot_strong`. Cloud authorization examines both models before runtime creation.
Namespaced Ollama tags such as `owner/model:tag` remain local; known LiteLLM cloud-provider prefixes
must be explicit.

## Idempotency and ambiguous outcomes

The gateway hashes the run ID, normalized domain, task, policy version, model, and exact compact
prompt into a request key. Raw prompts and raw model prose are not stored for idempotency. A
successful call stores at most 20 normalized pivot objects; an identical request replays those
objects without another reservation or SDK call.

If a reservation exists but no successful normalized result was committed—for example, the process
stopped during an upstream timeout—the request is ambiguous. ReconRelate suppresses an identical
retry and retains the original quota charge. Changed evidence changes the key and may be admitted as
a new request within the remaining hard budget.
