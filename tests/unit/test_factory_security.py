import pytest

from reconrelate.config.settings import Settings
from reconrelate.core.errors import SecurityError
from reconrelate.core.factory import build_runtime


def test_cloud_llm_blocked_when_env_disallows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECONRELATE_DB_PATH", ":memory:")
    monkeypatch.setenv("RECONRELATE_LLM_ALLOW_CLOUD", "false")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
    settings = Settings.from_env()
    with pytest.raises(SecurityError, match="Cloud LLM"):
        build_runtime(settings)


def test_ollama_allowed_when_cloud_disallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECONRELATE_LLM_ALLOW_CLOUD", "false")
    monkeypatch.setenv("LLM_MODEL", "qwen3.5:9b")
    monkeypatch.setenv("RECONRELATE_DB_PATH", ":memory:")
    settings = Settings.from_env()
    rt = build_runtime(settings)
    try:
        assert rt.repository is not None
    finally:
        rt.close()
