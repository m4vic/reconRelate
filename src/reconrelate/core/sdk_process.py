"""Bounded, killable execution for blocking third-party network SDKs."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from typing import Any

from reconrelate.core.errors import (
    ProviderAuthError,
    ProviderBudgetExceededError,
    ProviderError,
    ProviderMalformedError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)

_MAX_INPUT_BYTES = 65_536
_DEFAULT_MAX_OUTPUT_BYTES = 2_097_152
_ENV_ALLOWLIST = {
    "SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP", "TMPDIR",
    "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTP_PROXY", "HTTPS_PROXY",
    "NO_PROXY",
}


def _worker_environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key.upper() in _ENV_ALLOWLIST}


async def _bounded_read(reader: asyncio.StreamReader, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await reader.read(min(65_536, limit + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            raise ProviderMalformedError(f"SDK worker output exceeds {limit} bytes")


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=1.0)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


async def run_sdk_operation(
    operation: str,
    payload: dict[str, Any],
    *,
    timeout_sec: float = 30.0,
    max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
) -> dict[str, Any]:
    """Run one allowlisted SDK operation using JSON over stdin/stdout."""
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(encoded) > _MAX_INPUT_BYTES:
        raise ProviderMalformedError(f"SDK worker input exceeds {_MAX_INPUT_BYTES} bytes")

    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "reconrelate.data_gathering.sdk_worker",
        operation,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=_worker_environment(),
        **kwargs,
    )
    assert process.stdin is not None and process.stdout is not None
    try:
        process.stdin.write(encoded)
        await process.stdin.drain()
        process.stdin.close()
        read_task = asyncio.create_task(
            _bounded_read(process.stdout, max(1, int(max_output_bytes)))
        )
        wait_task = asyncio.create_task(process.wait())
        try:
            output, return_code = await asyncio.wait_for(
                asyncio.gather(read_task, wait_task), timeout=max(0.01, float(timeout_sec))
            )
        except asyncio.TimeoutError as exc:
            raise ProviderTimeoutError(
                f"SDK worker deadline exceeded for operation {operation}"
            ) from exc
        if return_code != 0:
            raise ProviderError(f"SDK worker exited with code {return_code} for {operation}")
        try:
            message = json.loads(output)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProviderMalformedError("SDK worker returned malformed JSON") from exc
        if not isinstance(message, dict):
            raise ProviderMalformedError("SDK worker response must be an object")
        if not message.get("ok"):
            error = message.get("error") or {}
            error_class = str(error.get("class") or "ProviderError")
            error_message = str(error.get("message") or "SDK worker failed")[:500]
            error_types = {
                "ProviderAuthError": ProviderAuthError,
                "ProviderBudgetExceededError": ProviderBudgetExceededError,
                "ProviderMalformedError": ProviderMalformedError,
                "ProviderRateLimitError": ProviderRateLimitError,
                "ProviderTimeoutError": ProviderTimeoutError,
            }
            error_type = error_types.get(error_class, ProviderError)
            raise error_type(f"{error_class}: {error_message}")
        result = message.get("result")
        if not isinstance(result, dict):
            raise ProviderMalformedError("SDK worker result must be an object")
        return result
    finally:
        await asyncio.shield(_stop_process(process))
