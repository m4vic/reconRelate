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
