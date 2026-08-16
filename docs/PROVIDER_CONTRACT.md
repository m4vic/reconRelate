# Provider adapter contract

ReconRelate separates provider-specific transport from orchestration, evidence, and persistence.
An adapter implements its capability's natural asynchronous operation (`lookup`, `search`, or
`related_orgs`) and is registered with a `ProviderInfo` manifest.

Each manifest declares:

- stable provider name and capability;
- free or paid tier and required environment keys;
- supported operations and result contract;
- whether an attempted call may be billable and its billing unit.
- maximum upstream requests and parsed pages per attempt.
- effective upstream `source_family` for independence accounting.

The source family identifies the evidence origin, not merely the adapter package. IANA RDAP and
legacy WHOIS both use `domain-registration-registry`; two rows from that cascade therefore count as
one family, not independent corroboration. Aggregators and wrappers should retain named upstream
attribution when possible. Use `unclassified` when lineage is genuinely unknown—unknown sources
are never presumed independent.

Capabilities may define an ordered cascade instead of selecting one provider. Registration is the
reference implementation: `rdap-iana` runs first and legacy WHOIS fills missing identity fields only
when needed. Every source still receives its own executor call, telemetry row, `ProviderResult`, and
observations. A composite record used for scoring must never be persisted as a substitute for the
individual evidence records.

External executable providers declare `requires_executable` and, when supported, an environment
variable containing an explicit path. Configuration-only diagnostics check presence but never run
the executable. Such adapters must use argument arrays without a shell, disable self-update and
active behavior, bound stdout/stderr/time, terminate on cancellation, validate structured output,
and document whether internal calls or paid quotas are opaque. Subfinder is the reference adapter.

Adapters must return the declared typed result or raise an exception. They must not convert network,
authentication, rate-limit, or malformed-response failures into an empty result. Empty means the
provider successfully found nothing. Use `ProviderAuthError`, `ProviderRateLimitError`, and
`ProviderMalformedError` when the transport does not expose a usable HTTP status.

The shared executor owns deadlines, bounded retries, result validation, circuit breaking, and call
telemetry. A provider must not add an independent unbounded retry loop. Every attempted billable
call is conservatively counted, including retries and failed attempts.

Each manifest also supplies a concurrency ceiling. The executor enforces this as an asynchronous
per-provider bulkhead, so a slow source cannot consume every in-process slot. Circuit failures are
stored in SQLite and therefore protect other ReconRelate processes using the same database. An
authentication failure opens the circuit immediately to avoid repeating paid calls with a bad key.

SQLite permits additionally enforce those ceilings across processes. Each actual provider attempt
must acquire a fixed-window rate token and an expiring concurrency lease. A process crash leaves a
lease only until its deadline. Rejection occurs before network I/O, records zero paid units, and does
not count as an upstream circuit failure. Manifest values are conservative ReconRelate safety
defaults, not claims about vendor quotas. Override them for a specific plan with:

```powershell
$env:RECONRELATE_PROVIDER_WHOXY_CONCURRENCY='2'
$env:RECONRELATE_PROVIDER_WHOXY_RATE_PER_MINUTE='20'
$env:RECONRELATE_PROVIDER_WHOXY_MAX_REQUESTS='1'
$env:RECONRELATE_PROVIDER_WHOXY_MAX_PAGES='1'
```

The normalized provider name shown by `providers doctor --json` determines the environment suffix.

The executor creates a fresh async-local request/page budget for every retry. `safe_get` consumes a
request before each initial or redirected HTTP call, so overflow stops before network I/O. An adapter
consumes a page when it accepts a response page for parsing. SDK-owned network libraries must call
`consume_request()` at their opaque call boundary and `consume_page()` for each visible page.
Budget overflow is non-retryable. Telemetry and exports distinguish logical attempts from actual
upstream requests and pages; this is necessary for cost review and for spotting unexpectedly chatty
free providers.

Contended calls join a durable FIFO queue in the same SQLite database instead of racing through
immediate retries. Only the oldest live waiter for a provider may consume the next rate token and
concurrency lease. Queue time does not increment provider attempts or paid units, and cancellation
removes the waiter. Stale waiters expire automatically after a crash. The wait is bounded to five
seconds by default; set `RECONRELATE_PROVIDER_CAPACITY_WAIT_SEC` to a non-negative number of seconds
to tune it. When the deadline expires, the call degrades explicitly as locally rate-limited without
opening the provider circuit.

Manifests also declare `max_response_bytes` and `max_result_items`. Direct HTTP adapters must use
the shared streaming reader, which checks `Content-Length` when present and stops with a typed error
as soon as a stream crosses its ceiling. They must not call an unbounded `response.read()` or
`response.text()`, and must not silently truncate JSON into apparently valid evidence. The executor
independently checks normalized dataclass/list collection sizes and serialized bytes before accepting
the result. Response-limit failures are non-retryable because repeating the same oversized response
only wastes time or paid units.

Direct HTTP adapters must create sessions with `safe_client_session` and issue requests with
`safe_get`; raw `aiohttp.ClientSession` and automatic redirects are not allowed in adapters. The
safe resolver validates the exact A/AAAA answers supplied to the connector, including every answer
in a mixed response. `safe_get` validates each redirect before following it, permits at most five
hops, and rejects HTTPS-to-HTTP downgrades. This policy prevents a successful URL precheck from being
bypassed by DNS rebinding or an unsafe redirect. Blocking libraries that own their sockets must use
`run_sdk_operation` with a fixed worker operation; `asyncio.to_thread` is not an acceptable timeout
boundary because cancellation cannot stop its socket. Worker input must use stdin, output must be
bounded JSON, and the adapter must consume its opaque request/page budget in the parent. Adding a
worker operation requires its own destination, input, output, timeout, and dependency review.

Set `RECONRELATE_DISABLE_PROVIDERS` to a comma-separated list of manifest names for an immediate,
reversible kill switch, for example `whoxy,duckduckgo`. Disabled capabilities return an explicit
empty provider result and the rest of the run continues; no call or paid unit is recorded.

Before adding an adapter:

1. Register its manifest and required keys without embedding secrets.
2. Add deterministic response fixtures for success, empty, auth failure, rate limit, malformed data,
   timeout, and response-size bounds.
3. Normalize its result into observations; do not write relationship conclusions directly.
4. Prove that its evidence adds unique verified recall in the offline evaluation corpus.
5. Verify `reconrelate providers doctor --json` without making a network or billable call.

`providers doctor` is intentionally configuration-only. Live health probes will require an explicit
opt-in and never spend paid credits:

```powershell
reconrelate providers doctor --live --target example.com
```

The target must be authorized and pass scan-target security validation. Supported free lookup and
subdomain adapters run concurrently under their normal timeout and response contracts. Paid
adapters are skipped before construction; reverse-WHOIS and acquisition adapters are skipped because
a domain alone is not a meaningful or safe input for those capabilities. Diagnostics return only
health state, latency, result count, and bounded errors—not provider payloads or collected PII.
