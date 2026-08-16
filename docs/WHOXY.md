# Whoxy reverse-WHOIS adapter

Whoxy is an optional BYOK source. It is never selected in the default `free` profile. Configure the
key locally, inspect the offline plan, then approve a bounded paid run:

```powershell
reconrelate config set key.WHOXY_API_KEY <key>
reconrelate providers doctor
reconrelate plan example.com --profile byok --approve-paid --max-billable-units 5
reconrelate run example.com --profile byok --approve-paid --max-billable-units 5
```

An account balance check is deliberately separate from scans and diagnostics:

```powershell
reconrelate providers balance --provider whoxy --approve-paid --max-billable-units 1
```

It makes exactly one request with retries disabled, returns only the reverse-WHOIS credit balance,
and does not persist the key or account response. Whoxy documents the `account=balance` endpoint but
does not state whether the request consumes a credit, so ReconRelate labels its billing effect
`unknown` and conservatively requires/reserves one unit before making the request. Missing approval,
budget, or key fails before provider construction or network access.

The adapter uses Whoxy's privacy-reduced `mode=micro`, which returns historical domain records but
not registrant contact fields. It performs one request and accepts one response page per attempt,
with a 1 MiB response ceiling and the documented 2,500-row micro ceiling. ReconRelate then
normalizes and deduplicates domains and returns only the caller's requested count. Historical matches
are candidates, not current ownership proof; normal current-evidence verification still applies.

According to [Whoxy's official reverse-WHOIS documentation](https://www.whoxy.com/reverse-whois/),
each successfully fetched result page consumes one API credit, while an explicit no-result response
does not. ReconRelate reserves one worst-case billable unit before every attempted lookup and does
not refund it after failures or empty results, because local accounting cannot authoritatively
reconcile the provider's balance. Use `providers value` and matched benchmarks to measure whether
those calls add verified findings.

Whoxy's [terms](https://www.whoxy.com/terms.php) state that source WHOIS restrictions still apply,
prohibit abusive or unauthorized use and service resale, and allow terms/prices to change. Results
remain in the user's originating local run and are excluded from the shared cross-run cache.
Portable graph exports retain derived claims and restricted attribution references but omit the
underlying Whoxy observation fields. See the enforced [provider data-use policy](PROVIDER_DATA_POLICY.md).
The open-source project does not ship or aggregate Whoxy data. Users remain responsible for their
agreement, source restrictions, retention, and any redistribution of their own exports. This is
operational documentation, not legal advice.

The adapter never logs pivot values or API keys. Missing credentials, HTTP authentication/rate
failures, malformed responses, unknown status-zero errors, and response-limit violations are distinct
typed failures. Only explicit no-record/no-result status reasons become a successful empty result.

No paid health probe is performed by `providers doctor --live`; paid providers remain skipped.
Ordinary `providers doctor` and `providers list` remain offline and never check account balances.
