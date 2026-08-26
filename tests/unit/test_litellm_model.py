from reconrelate.llm_orchestration.relationship_engine import _litellm_model_id


def test_bare_tag_prefixed_ollama() -> None:
    assert _litellm_model_id("qwen2.5:7b-instruct") == "ollama/qwen2.5:7b-instruct"


def test_provider_slash_passthrough() -> None:
    assert _litellm_model_id("ollama/gemma4:latest") == "ollama/gemma4:latest"


def test_openai_style_passthrough() -> None:
    assert _litellm_model_id("gpt-4o-mini") == "gpt-4o-mini"


def test_claude_gets_anthropic_prefix() -> None:
    assert _litellm_model_id("claude-3-5-sonnet-20241022") == "anthropic/claude-3-5-sonnet-20241022"


def test_empty_defaults() -> None:
    assert _litellm_model_id("") == "ollama/qwen2.5:7b-instruct"


def test_namespaced_ollama_model_is_not_mistaken_for_cloud_provider() -> None:
    assert _litellm_model_id("mannix/llama3.1:8b") == "ollama/mannix/llama3.1:8b"


def test_explicit_known_cloud_provider_prefix_is_preserved() -> None:
    assert _litellm_model_id("openrouter/vendor/model") == "openrouter/vendor/model"


def test_ollama_calls_disable_thinking_mode() -> None:
    """Qwen3-family models think by default and return EMPTY content, spending the whole
    budget in the `thinking` field - a capable model then looks permanently broken.
    Measured on qwen3.5:9b: think=True -> 3815 chars thinking / 0 content; think=False ->
    valid schema JSON. Verified a no-op for non-thinking models.
    """
    import asyncio
    from unittest.mock import patch

    from reconrelate.llm_orchestration.relationship_engine import LLMClient

    captured: dict = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop here - we only care about the kwargs")

    client = LLMClient(model="qwen3.5:9b")
    with patch("litellm.completion", side_effect=fake_completion):
        asyncio.run(client._call_model(
            "acme.com", {"domain": "acme.com"}, None, model="qwen3.5:9b", task="relationship_pivot"
        ))

    assert captured["model"].startswith("ollama/")
    assert captured["think"] is False
    # Do not allow Ollama's small server default to silently truncate the system prompt.
    from reconrelate.llm_orchestration.prompt_builder import OLLAMA_NUM_CTX
    assert captured["num_ctx"] == OLLAMA_NUM_CTX


def test_local_evidence_budget_fits_the_explicit_ollama_window() -> None:
    """The evidence limit and the request's Ollama context setting must stay coupled."""
    from reconrelate.llm_orchestration.prompt_builder import (
        MAX_LLM_CONTEXT_CHARS,
        OLLAMA_NUM_CTX,
        SYSTEM_PROMPT,
    )

    # This is intentionally conservative: chars/4 understates JSON and template token counts,
    # hence prompt_builder also reserves 256 template tokens.
    worst_case_tokens = len(SYSTEM_PROMPT) // 4 + MAX_LLM_CONTEXT_CHARS // 4 + 512 + 256
    assert worst_case_tokens <= OLLAMA_NUM_CTX


def test_cloud_calls_do_not_send_the_ollama_think_flag() -> None:
    # `think` is Ollama-specific; forwarding it to OpenAI/Gemini/Anthropic would be rejected.
    import asyncio
    from unittest.mock import patch

    from reconrelate.llm_orchestration.relationship_engine import LLMClient

    captured: dict = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop here")

    client = LLMClient(model="gpt-5-mini")
    with patch("litellm.completion", side_effect=fake_completion):
        asyncio.run(client._call_model(
            "acme.com", {"domain": "acme.com"}, None, model="gpt-5-mini", task="relationship_pivot"
        ))

    assert "think" not in captured
