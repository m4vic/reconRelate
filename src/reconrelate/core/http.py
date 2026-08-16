"""Bounded HTTP response readers shared by network provider adapters."""

from __future__ import annotations

import json
from typing import Any

from reconrelate.core.errors import ProviderMalformedError, ProviderResponseLimitError


async def read_limited_bytes(response: Any, *, max_bytes: int) -> bytes:
    limit = max(1, int(max_bytes))
    content_length = response.headers.get("Content-Length")
    if content_length:
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
            raise ProviderResponseLimitError(f"streamed response exceeds {limit} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


async def read_limited_text(response: Any, *, max_bytes: int) -> str:
    return (await read_limited_bytes(response, max_bytes=max_bytes)).decode(
        response.charset or "utf-8", errors="replace"
    )


async def read_limited_json(response: Any, *, max_bytes: int) -> Any:
    text = await read_limited_text(response, max_bytes=max_bytes)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderMalformedError("provider returned malformed JSON") from exc
