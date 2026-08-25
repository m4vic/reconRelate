"""Bounded HTTP response readers shared by network provider adapters."""

from __future__ import annotations

import json
from typing import Any

from reconrelate.core.errors import ProviderMalformedError, ProviderResponseLimitError


async def read_limited_bytes(
    response: Any, *, max_bytes: int, truncate: bool = False
) -> bytes:
    """Read at most `max_bytes`, rejecting oversized responses by default.

    `truncate=True` returns the first `max_bytes` instead of raising. Only appropriate where a
    prefix is genuinely useful on its own — HTML signal extraction reads <title>, meta
    description, tracker ids and the copyright entity, which sit in <head> or early markup, so a
    2 MB marketing page still yields every signal from its first chunk. For an API response
    parsed as a whole (JSON), a prefix is meaningless and rejecting is correct.

    The byte ceiling is unchanged either way: this never reads more, it only decides whether a
    partial read is an error or a usable result.
    """
    limit = max(1, int(max_bytes))
    content_length = response.headers.get("Content-Length")
    if content_length and not truncate:
        try:
            if int(content_length) > limit:
                raise ProviderResponseLimitError(
                    f"response Content-Length {content_length} exceeds {limit} bytes"
                )
        except ValueError:
            pass
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.content.iter_chunked(min(65_536, limit)):
        total += len(chunk)
        if total > limit:
            if truncate:
                chunks.append(chunk)
                break
            raise ProviderResponseLimitError(f"streamed response exceeds {limit} bytes")
        chunks.append(chunk)
    payload = b"".join(chunks)
    return payload[:limit] if truncate else payload


async def read_limited_text(response: Any, *, max_bytes: int, truncate: bool = False) -> str:
    return (
        await read_limited_bytes(response, max_bytes=max_bytes, truncate=truncate)
    ).decode(response.charset or "utf-8", errors="replace")


async def read_limited_json(response: Any, *, max_bytes: int) -> Any:
    text = await read_limited_text(response, max_bytes=max_bytes)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderMalformedError("provider returned malformed JSON") from exc
