"""Optional ProjectDiscovery Subfinder integration with per-source provenance."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from reconrelate.core.errors import ProviderError, ProviderMalformedError, ProviderTimeoutError
from reconrelate.core.normalize import normalize_domain
from reconrelate.core.provider_budget import consume_page, consume_request
from reconrelate.core.types import SubdomainFinding
from reconrelate.security.safe_target import validate_scan_target

_DEFAULT_SOURCES = "crtsh,alienvault,commoncrawl,waybackarchive"
_SOURCE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_MAX_STDOUT = 4_194_304
_MAX_STDERR = 65_536


async def _read_bounded(reader: asyncio.StreamReader, limit: int, label: str) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await reader.read(min(65_536, limit + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            raise ProviderMalformedError(f"Subfinder {label} exceeds {limit} bytes")


async def _stop(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=1.0)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


def _sources_from_env() -> list[str]:
    raw = os.getenv("RECONRELATE_SUBFINDER_SOURCES", _DEFAULT_SOURCES)
    values = [value.strip().lower() for value in raw.split(",") if value.strip()]
    if not values or len(values) > 20 or any(not _SOURCE_RE.fullmatch(value) for value in values):
        raise ProviderMalformedError(
            "RECONRELATE_SUBFINDER_SOURCES must contain 1..20 comma-separated source names"
        )
    return list(dict.fromkeys(values))


def _rate_limit_from_env() -> int:
    try:
        return max(1, min(int(os.getenv("RECONRELATE_SUBFINDER_RATE_PER_SECOND", "5")), 100))
    except ValueError as exc:
        raise ProviderMalformedError("RECONRELATE_SUBFINDER_RATE_PER_SECOND must be an integer") from exc


class SubfinderProvider:
    def __init__(self, executable: str | None = None, *, wall_timeout_sec: float = 20.0) -> None:
        configured = executable or os.getenv("RECONRELATE_SUBFINDER_PATH", "").strip()
        resolved = str(Path(configured).expanduser().resolve()) if configured else shutil.which("subfinder")
        usable = bool(resolved) and Path(str(resolved)).is_file() and (
            os.name == "nt" or os.access(str(resolved), os.X_OK)
        )
        if not usable:
            raise ProviderError("Subfinder executable is not installed or configured")
        self.executable = str(resolved)
        self.wall_timeout_sec = max(0.01, float(wall_timeout_sec))

    @staticmethod
    def _parse(stdout: bytes, root_domain: str, max_results: int) -> list[SubdomainFinding]:
        found: dict[str, set[str]] = {}
        for line_number, raw_line in enumerate(stdout.splitlines(), start=1):
            if not raw_line.strip():
                continue
            try:
                item = json.loads(raw_line)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ProviderMalformedError(
                    f"Subfinder emitted malformed JSONL at line {line_number}"
                ) from exc
            if not isinstance(item, dict):
                raise ProviderMalformedError("Subfinder JSONL entries must be objects")
            raw_host = str(item.get("host") or item.get("domain") or "").strip().lower()
            try:
                host = normalize_domain(raw_host)
            except Exception:
                continue
            if host == root_domain or not host.endswith("." + root_domain):
                continue
            raw_sources: Any = item.get("sources") or item.get("source") or []
            if isinstance(raw_sources, str):
                raw_sources = raw_sources.split(",")
            sources = {
                str(value).strip().lower()
                for value in raw_sources if isinstance(raw_sources, list)
                if _SOURCE_RE.fullmatch(str(value).strip().lower())
            }
            found.setdefault(host, set()).update(sources or {"unknown"})
        return [
            SubdomainFinding(domain=domain, sources=sorted(sources))
            for domain, sources in sorted(found.items())[:max(1, max_results)]
        ]

    async def search(self, domain: str, max_results: int = 15) -> list[SubdomainFinding]:
        domain = normalize_domain(domain)
        validate_scan_target(domain)
        sources = _sources_from_env()
        rate_limit = _rate_limit_from_env()
        command = [
            self.executable,
            "-d", domain,
            "-json",
            "-collect-sources",
            "-silent",
            "-disable-update-check",
            "-sources", ",".join(sources),
            "-rate-limit", str(rate_limit),
            "-timeout", "5",
            "-max-time", "1",
        ]
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        consume_request()  # One opaque, internally rate-limited passive-source invocation.
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **kwargs,
        )
        assert process.stdout is not None and process.stderr is not None
        try:
            stdout_task = asyncio.create_task(_read_bounded(process.stdout, _MAX_STDOUT, "stdout"))
            stderr_task = asyncio.create_task(_read_bounded(process.stderr, _MAX_STDERR, "stderr"))
            wait_task = asyncio.create_task(process.wait())
            try:
                stdout, stderr, return_code = await asyncio.wait_for(
                    asyncio.gather(stdout_task, stderr_task, wait_task),
                    timeout=self.wall_timeout_sec,
                )
            except asyncio.TimeoutError as exc:
                raise ProviderTimeoutError(
                    f"Subfinder exceeded its {self.wall_timeout_sec:g} second wall-clock deadline"
                ) from exc
            if return_code != 0:
                message = stderr.decode("utf-8", errors="replace").strip()[:500]
                raise ProviderError(f"Subfinder exited with code {return_code}: {message}")
            findings = self._parse(stdout, domain, max_results)
            consume_page()  # One bounded JSONL result stream.
            return findings
        finally:
            await asyncio.shield(_stop(process))
