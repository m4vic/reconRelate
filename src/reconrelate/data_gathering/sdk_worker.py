"""Private JSON worker for blocking SDK operations. Invoked by sdk_process only."""

from __future__ import annotations

import contextlib
from dataclasses import asdict
import json
import os
import sys
import time
from collections.abc import Mapping
from typing import Any

from reconrelate.core.provider_budget import provider_budget


def _whois(payload: dict[str, Any]) -> dict[str, Any]:
    domain = str(payload.get("domain") or "").strip()
    if not domain:
        raise ValueError("domain is required")
    try:
        import whois  # type: ignore
    except ImportError:
        return {"available": False, "record": {}}
    data = whois.whois(domain)
    return {
        "available": True,
        "record": dict(data) if isinstance(data, Mapping) else {},
    }


def _duckduckgo(payload: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query") or "").strip()
    limit = max(1, min(int(payload.get("limit") or 10), 100))
    if not query:
        raise ValueError("query is required")
    try:
        from duckduckgo_search import DDGS  # type: ignore
    except ImportError:
        return {"available": False, "results": []}
    rows: list[dict[str, str]] = []
    with DDGS() as ddgs:
        for item in ddgs.text(query, max_results=limit):
            if not isinstance(item, Mapping):
                continue
            rows.append({
                "href": str(item.get("href") or "")[:4096],
                "body": str(item.get("body") or "")[:8192],
                "title": str(item.get("title") or "")[:1024],
            })
            if len(rows) >= limit:
                break
    return {"available": True, "results": rows}


def _protocol_health(payload: dict[str, Any]) -> dict[str, Any]:
    delay_ms = max(0, min(int(payload.get("delay_ms") or 0), 5_000))
    if delay_ms:
        time.sleep(delay_ms / 1000)
    return {
        "pid": os.getpid(),
        "echo": str(payload.get("echo") or "")[:100],
        "sensitive_environment_present": any(
            key in os.environ for key in ("OPENAI_API_KEY", "WHOXY_API_KEY", "GITHUB_TOKEN")
        ),
    }


def _dns(payload: dict[str, Any]) -> dict[str, Any]:
    from reconrelate.data_gathering.dns_provider import DNSProvider

    domain = str(payload.get("domain") or "").strip()
    if not domain:
        raise ValueError("domain is required")
    max_requests = max(1, min(int(payload.get("max_requests") or 6), 20))
    max_pages = max(1, min(int(payload.get("max_pages") or 6), 20))
    with provider_budget(max_requests=max_requests, max_pages=max_pages) as budget:
        record = DNSProvider()._lookup_sync(domain)
    return {"record": asdict(record), "requests": budget.requests, "pages": budget.pages}


_OPERATIONS = {
    "whois": _whois,
    "duckduckgo": _duckduckgo,
    "dns": _dns,
    "protocol_health": _protocol_health,
}


def main() -> int:
    operation = sys.argv[1] if len(sys.argv) == 2 else ""
    try:
        raw = sys.stdin.buffer.read(65_537)
        if len(raw) > 65_536:
            raise ValueError("input exceeds 65536 bytes")
        payload = json.loads(raw or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("input must be an object")
        handler = _OPERATIONS.get(operation)
        if handler is None:
            raise ValueError(f"unknown SDK operation: {operation}")
        with open(os.devnull, "w", encoding="utf-8") as sink, contextlib.redirect_stdout(sink):
            result = handler(payload)
        message = {"ok": True, "result": result}
    except Exception as exc:
        message = {
            "ok": False,
            "error": {"class": type(exc).__name__, "message": str(exc)[:500]},
        }
    output = json.dumps(message, separators=(",", ":"), default=str).encode("utf-8")
    # Use the original stdout because dependency output was redirected only inside the handler.
    sys.stdout.buffer.write(output)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
