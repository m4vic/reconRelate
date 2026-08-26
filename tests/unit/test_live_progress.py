import asyncio
import io
import sys

from reconrelate.orchestrator.orchestrator import RunOrchestrator


class _TTYBuffer(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.flush_count = 0

    def isatty(self) -> bool:
        return True

    def flush(self) -> None:
        self.flush_count += 1


def test_live_progress_updates_and_flushes_a_tty(monkeypatch) -> None:
    """A local model wait remains visibly alive rather than looking like a hung scan."""
    output = _TTYBuffer()
    monkeypatch.setattr(sys, "stderr", output)
    orchestrator = object.__new__(RunOrchestrator)

    async def exercise() -> None:
        async with orchestrator._live_progress(1, 0, 0, 1, "example.com", "analyzing"):
            await asyncio.sleep(0.45)

    asyncio.run(exercise())

    assert "example.com [analyzing" in output.getvalue()
    assert output.flush_count >= 1
