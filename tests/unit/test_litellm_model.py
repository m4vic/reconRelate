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
