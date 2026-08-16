# Wayback historical web evidence

ReconRelate can inspect a small, deterministic sample of archived root pages from the Internet
Archive. No key or account is required.

```console
reconrelate history example.com --max 4
reconrelate history example.com --max 2 --json
reconrelate run example.com --history
```

Normal scans do not call the archive unless `--history` or `RECONRELATE_HISTORICAL_WEB=true` is
set. This avoids adding up to eight archive requests per mapped domain before the query planner can
make evidence-gap decisions.

## Sampling and limits

For the exact HTTPS roots of the bare domain and `www` host, the adapter asks the Wayback CDX API
for up to two earliest and two latest successful HTML captures, collapsed by content digest. It
deduplicates the combined result and fetches at most four replay pages. Each response is capped at
one MiB, and the manifest enforces eight requests/pages, one concurrent call, and a 90-second outer
deadline.

Each record preserves the capture timestamp, original URL, replay URL, content digest, title,
tracker IDs, and copyright organization. Observations use explicitly historical predicates such as
`historically_used_tracker` and `historically_claimed_copyright_org`, with the capture time as both
observation and validity start.

Historical evidence is not proof of current control and is not automatically fed into current
reverse-search pivots. It is useful for explaining past relationships, detecting rebrands or domain
transfers, and corroborating other time-compatible evidence.

TLS interception by a local network can require its CA certificate to be configured through the
Python/OpenSSL trust settings (for example `SSL_CERT_FILE`). ReconRelate does not disable certificate
verification to work around an untrusted chain.
