# Network security model

ReconRelate treats domains, provider responses, redirect locations, HTML, WHOIS fields, and model
inputs as untrusted data.

All direct HTTP provider adapters use the shared safe transport. It provides:

- HTTP/HTTPS-only URLs with no embedded credentials;
- hostname and literal-IP deny rules for loopback, private, link-local, reserved, multicast,
  documentation, metadata, and internal-style targets;
- an aiohttp resolver that validates the exact A/AAAA addresses returned to the connector;
- fail-closed handling when even one DNS answer is non-public;
- automatic redirects disabled and a maximum of five manually followed hops;
- target validation on every redirect, including relative redirects;
- rejection of HTTPS-to-HTTP downgrade redirects;
- hard streaming response limits before parsing.

This closes the common DNS-rebinding time-of-check/time-of-use gap: aiohttp connects only to the
addresses returned by the validating resolver, rather than resolving once for a preflight check and
again inside an unrelated connector. Connector DNS caching retains only already validated answers.

Security-policy failures are non-retryable. They appear as provider errors, may make a run degraded,
and never cause a second outbound attempt.

The standard-library DNS provider may report private DNS answers as evidence, but does not connect
to those addresses. Any subsequent HTTP connection still passes through the safe transport.

WHOIS, DNS, and DuckDuckGo libraries own blocking sockets and cannot use the aiohttp resolver. They
therefore execute in short-lived worker processes with a fixed operation allowlist. Lookup payloads
travel over bounded stdin rather than process arguments, dependency stdout is discarded, worker
JSON is size-limited, unrelated secret environment variables are not inherited, and timeout or
cancellation terminates the process. This provides a killable resource boundary; it does not make
those third-party libraries equivalent to the validating HTTP transport. Their destinations remain
fixed by the adapter and their inputs are validated in the parent.

Active port probing is not implemented. Users must still run ReconRelate only against authorized
targets.
