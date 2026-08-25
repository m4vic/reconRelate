import asyncio

import pytest

from reconrelate.core.errors import ProviderMalformedError, ProviderResponseLimitError
from reconrelate.core.http import read_limited_bytes, read_limited_json


class _Content:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def iter_chunked(self, size: int):
        for chunk in self.chunks:
            yield chunk


class _Response:
    def __init__(self, chunks: list[bytes], content_length: str | None = None) -> None:
        self.content = _Content(chunks)
        self.headers = {} if content_length is None else {"Content-Length": content_length}
        self.charset = "utf-8"


def test_content_length_over_limit_fails_before_stream_read() -> None:
    response = _Response([b"small"], content_length="100")
    with pytest.raises(ProviderResponseLimitError, match="Content-Length"):
        asyncio.run(read_limited_bytes(response, max_bytes=10))


def test_stream_over_limit_fails_closed_without_truncation() -> None:
    response = _Response([b"12345", b"67890", b"x"])
    with pytest.raises(ProviderResponseLimitError, match="streamed"):
        asyncio.run(read_limited_bytes(response, max_bytes=10))


def test_bounded_json_reader_rejects_malformed_payload() -> None:
    response = _Response([b"not-json"])
    with pytest.raises(ProviderMalformedError, match="malformed JSON"):
        asyncio.run(read_limited_json(response, max_bytes=100))


class _TruncResponse:
    """Minimal aiohttp-like response whose body exceeds the byte ceiling."""

    def __init__(self, payload: bytes, content_length: str | None = None) -> None:
        self._payload = payload
        self.headers = {"Content-Length": content_length} if content_length else {}
        self.charset = "utf-8"
        self.content = self

    async def iter_chunked(self, size):  # noqa: ANN001
        for i in range(0, len(self._payload), size):
            yield self._payload[i:i + size]


def test_truncate_returns_prefix_instead_of_raising() -> None:
    import asyncio
    from reconrelate.core.http import read_limited_bytes

    body = b"<title>Acme</title>" + b"x" * 200_000
    out = asyncio.run(read_limited_bytes(_TruncResponse(body), max_bytes=1_000, truncate=True))
    assert len(out) == 1_000
    assert out.startswith(b"<title>Acme</title>")


def test_truncate_ignores_content_length_rejection() -> None:
    # A large corporate page advertises its full size; truncating must still succeed.
    import asyncio
    from reconrelate.core.http import read_limited_bytes

    body = b"y" * 50_000
    out = asyncio.run(
        read_limited_bytes(_TruncResponse(body, content_length="50000"), max_bytes=1_000, truncate=True)
    )
    assert len(out) == 1_000


def test_default_still_rejects_oversized_response() -> None:
    import asyncio
    import pytest as _pytest
    from reconrelate.core.errors import ProviderResponseLimitError
    from reconrelate.core.http import read_limited_bytes

    body = b"z" * 50_000
    with _pytest.raises(ProviderResponseLimitError):
        asyncio.run(read_limited_bytes(_TruncResponse(body), max_bytes=1_000))


def test_truncate_leaves_small_responses_untouched() -> None:
    import asyncio
    from reconrelate.core.http import read_limited_bytes

    body = b"<title>Small</title>"
    out = asyncio.run(read_limited_bytes(_TruncResponse(body), max_bytes=1_000, truncate=True))
    assert out == body
