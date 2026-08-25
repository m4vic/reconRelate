from datetime import date

import pytest

from reconrelate.cli.app import _normalize_cli_argv, main
from reconrelate.config import config_file as cf
from reconrelate.config import model_profiles as mp
from reconrelate.config.settings import Settings
from reconrelate.llm_orchestration import model_pricing


@pytest.fixture()
def cfg_path(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    monkeypatch.setenv("RECONRELATE_CONFIG_PATH", str(p))
    return p


@pytest.fixture(autouse=True)
def _clear_runtime_prices():
    # register_price mutates module-level shared state; keep tests isolated from each other
    # and from anything else in the suite that might register a price.
    model_pricing._RUNTIME_PRICES.clear()
    yield
    model_pricing._RUNTIME_PRICES.clear()


# ---- provider inference -----------------------------------------------------------------

def test_infer_provider() -> None:
    assert mp.infer_provider("qwen2.5:7b-instruct") == "ollama"
    assert mp.infer_provider("ollama/llama3") == "ollama"
    assert mp.infer_provider("gpt-5-mini") == "openai"
    assert mp.infer_provider("claude-3-5-sonnet") == "anthropic"
    assert mp.infer_provider("groq/llama3-70b") == "custom"


# ---- litellm id resolution / idempotency ------------------------------------------------

def test_ollama_profile_litellm_id() -> None:
    profile = mp.ModelProfile(name="x", provider="ollama", model_id="qwen2.5:7b-instruct")
    assert profile.litellm_id() == "ollama/qwen2.5:7b-instruct"
    assert profile.is_cloud() is False


def test_custom_profile_bare_model_gets_openai_prefix_and_is_idempotent() -> None:
    from reconrelate.llm_orchestration.relationship_engine import _litellm_model_id

    profile = mp.ModelProfile(name="x", provider="custom", model_id="my-hosted-model")
    resolved = profile.litellm_id()
    assert resolved == "openai/my-hosted-model"
    # Re-deriving downstream (as call_unified does on every call) must reproduce the same
    # string, or a custom profile would silently be reclassified as local Ollama.
    assert _litellm_model_id(resolved) == resolved
    assert profile.is_cloud() is True


def test_custom_profile_prequalified_model_left_verbatim() -> None:
    profile = mp.ModelProfile(name="x", provider="custom", model_id="together_ai/meta-llama/Llama-3-70b")
    assert profile.litellm_id() == "together_ai/meta-llama/Llama-3-70b"


# ---- add / price envelope requirement ----------------------------------------------------

def test_add_ollama_profile_needs_no_price() -> None:
    store = mp.ProfileStore()
    profile = mp.add_profile(store, name="local", provider="ollama", model_id="qwen2.5:7b-instruct")
    assert profile.input_usd_per_million is None
    assert "local" in store.profiles


def test_add_builtin_priced_cloud_model_needs_no_extra_price() -> None:
    store = mp.ProfileStore()
    profile = mp.add_profile(store, name="oai", provider="openai", model_id="gpt-5-mini")
    assert profile.input_usd_per_million is None  # relies on the built-in catalog


def test_add_unpriced_cloud_model_fails_loud() -> None:
    store = mp.ProfileStore()
    with pytest.raises(mp.ModelProfileError, match="no known price envelope"):
        mp.add_profile(store, name="claude", provider="anthropic", model_id="claude-3-5-sonnet")


def test_add_unpriced_cloud_model_succeeds_with_explicit_price() -> None:
    store = mp.ProfileStore()
    profile = mp.add_profile(
        store, name="claude", provider="anthropic", model_id="claude-3-5-sonnet",
        input_price=3.0, output_price=15.0,
    )
    assert profile.price_verified_on == date.today().isoformat()


def test_add_rejects_unknown_provider_and_missing_name() -> None:
    store = mp.ProfileStore()
    with pytest.raises(mp.ModelProfileError):
        mp.add_profile(store, name="x", provider="bogus", model_id="m")
    with pytest.raises(mp.ModelProfileError):
        mp.add_profile(store, name="  ", provider="ollama", model_id="m")


# ---- use / remove / roles -----------------------------------------------------------------

def test_use_assigns_role_and_remove_clears_it() -> None:
    store = mp.ProfileStore()
    mp.add_profile(store, name="local", provider="ollama", model_id="qwen2.5:7b-instruct")
    mp.use_profile(store, "local", "fast")
    assert mp.active_profile(store, "fast").name == "local"

    mp.remove_profile(store, "local")
    assert "local" not in store.profiles
    assert mp.active_profile(store, "fast") is None


def test_use_unknown_profile_or_role_raises() -> None:
    store = mp.ProfileStore()
    mp.add_profile(store, name="local", provider="ollama", model_id="qwen2.5:7b-instruct")
    with pytest.raises(mp.ModelProfileError):
        mp.use_profile(store, "nope", "primary")
    with pytest.raises(mp.ModelProfileError):
        mp.use_profile(store, "local", "adjudicator")


# ---- persistence round-trip ----------------------------------------------------------------

def test_save_and_load_round_trip(cfg_path) -> None:
    store = mp.ProfileStore()
    mp.add_profile(store, name="local", provider="ollama", model_id="qwen2.5:7b-instruct", api_base="http://x:1234")
    mp.add_profile(
        store, name="mini", provider="openai", model_id="gpt-5-mini", key_env="OPENAI_API_KEY",
    )
    mp.use_profile(store, "local", "fast")
    mp.use_profile(store, "mini", "primary")
    mp.save_store(store)

    reloaded = mp.load_store()
    assert set(reloaded.profiles) == {"local", "mini"}
    assert reloaded.roles == {"fast": "local", "primary": "mini"}
    assert reloaded.profiles["local"].api_base == "http://x:1234"


def test_load_store_on_corrupt_json_raises() -> None:
    with pytest.raises(mp.ModelProfileError):
        mp.ProfileStore.from_json("{not json")


def test_load_store_drops_roles_pointing_at_missing_profiles() -> None:
    raw = (
        '{"schema_version": 1, "profiles": {"a": {"name": "a", "provider": "ollama", '
        '"model_id": "m"}}, "roles": {"primary": "a", "fast": "ghost"}}'
    )
    store = mp.ProfileStore.from_json(raw)
    assert store.roles == {"primary": "a"}


# ---- env expansion --------------------------------------------------------------------------

def test_apply_profiles_to_env_expands_roles_and_never_overrides_real_env() -> None:
    store = mp.ProfileStore()
    mp.add_profile(store, name="local", provider="ollama", model_id="qwen2.5:7b-instruct", api_base="http://x:1234")
    mp.add_profile(store, name="mini", provider="openai", model_id="gpt-5-mini")
    mp.use_profile(store, "local", "fast")
    mp.use_profile(store, "mini", "primary")

    env: dict[str, str] = {}
    mp.apply_profiles_to_env(store, env)
    assert env["LLM_MODEL"] == "gpt-5-mini"
    assert env["FAST_LLM_MODEL"] == "ollama/qwen2.5:7b-instruct"
    assert env["OLLAMA_API_BASE"] == "http://x:1234"

    env2 = {"LLM_MODEL": "already-set"}
    mp.apply_profiles_to_env(store, env2)
    assert env2["LLM_MODEL"] == "already-set"  # real/pre-set env wins, same as apply_config_to_env


def test_apply_profiles_to_env_registers_custom_price_and_expands_custom_api_base() -> None:
    store = mp.ProfileStore()
    mp.add_profile(
        store, name="hosted", provider="custom", model_id="my-model", api_base="http://10.0.0.5:8000",
        input_price=1.0, output_price=2.0,
    )
    mp.use_profile(store, "hosted", "primary")

    env: dict[str, str] = {}
    mp.apply_profiles_to_env(store, env)
    assert env["LLM_MODEL"] == "openai/my-model"
    assert env["RECONRELATE_LLM_CUSTOM_API_BASE"] == "http://10.0.0.5:8000"

    # The registered price must be usable at estimate time under the exact same key.
    cost = model_pricing.estimate_cloud_cost_microusd("openai/my-model", 1_000_000, 0)
    assert cost == 1_000_000  # $1.00/M input * 1,000,000 tokens


def test_apply_profiles_to_env_noop_with_no_roles_assigned() -> None:
    store = mp.ProfileStore()
    mp.add_profile(store, name="local", provider="ollama", model_id="qwen2.5:7b-instruct")
    env: dict[str, str] = {}
    mp.apply_profiles_to_env(store, env)
    assert env == {}


# ---- CLI ---------------------------------------------------------------------------------

def test_normalize_cli_argv_does_not_swallow_model_command() -> None:
    assert _normalize_cli_argv(["model", "add", "local", "qwen2.5:7b-instruct"])[0] == "model"


def test_model_add_use_list_remove_cli_round_trip(cfg_path, capsys) -> None:
    assert main(["model", "add", "local", "qwen2.5:7b-instruct"]) == 0
    assert main(["model", "use", "local", "--role", "primary"]) == 0
    capsys.readouterr()

    assert main(["model", "list", "--json"]) == 0
    import json
    payload = json.loads(capsys.readouterr().out)
    assert payload["roles"] == {"primary": "local"}
    assert payload["profiles"]["local"]["provider"] == "ollama"
    capsys.readouterr()

    assert main(["model", "remove", "local"]) == 0
    capsys.readouterr()
    assert main(["model", "list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["profiles"] == {}


def test_model_add_cli_reports_missing_price_as_error(cfg_path, capsys) -> None:
    code = main(["model", "add", "claude", "claude-3-5-sonnet", "--provider", "anthropic"])
    assert code == 1
    assert "no known price envelope" in capsys.readouterr().err


def test_model_use_cli_warns_about_cloud_spend_gates(cfg_path, capsys) -> None:
    main(["model", "add", "mini", "gpt-5-mini", "--provider", "openai"])
    capsys.readouterr()
    main(["model", "use", "mini"])
    out = capsys.readouterr().out
    assert "--approve-cloud" in out


def test_model_add_key_value_stores_secret_via_existing_config_mechanism(cfg_path) -> None:
    code = main([
        "model", "add", "mini", "gpt-5-mini", "--provider", "openai", "--key-value", "sk-testsecret",
    ])
    assert code == 0
    assert cf.load_config().get("OPENAI_API_KEY") == "sk-testsecret"


def test_model_use_selection_flows_into_settings_via_apply_profiles_to_env(cfg_path, monkeypatch) -> None:
    monkeypatch.delenv("LLM_MODEL", raising=False)
    main(["model", "add", "local", "qwen2.5:7b-instruct", "--api-base", "http://custom:9999"])
    main(["model", "use", "local", "--role", "primary"])
    # main() itself calls apply_profiles_to_env(); reproduce that step directly here.
    cf.apply_config_to_env()
    mp.apply_profiles_to_env()
    settings = Settings.from_env()
    assert settings.llm_model == "ollama/qwen2.5:7b-instruct"
    assert settings.ollama_api_base == "http://custom:9999"


# ---- gemini provider (regression: bare gemini names fell through to ollama/) -------------

def test_gemini_profile_gets_gemini_prefix_and_is_idempotent() -> None:
    from reconrelate.llm_orchestration.relationship_engine import _litellm_model_id

    p = mp.ModelProfile(name="g", provider="gemini", model_id="gemini-3.6-flash")
    resolved = p.litellm_id()
    assert resolved == "gemini/gemini-3.6-flash"
    # Without the gemini branch this resolved to "ollama/gemini-3.6-flash" and silently tried
    # to reach a local daemon. Re-derivation downstream must be stable.
    assert _litellm_model_id(resolved) == resolved
    assert p.is_cloud() is True


def test_gemini_provider_is_inferred_from_a_bare_model_name() -> None:
    assert mp.infer_provider("gemini-3.6-flash") == "gemini"
    assert mp.infer_provider("gemini/gemini-3.6-flash") == "gemini"


def test_gemini_default_key_env() -> None:
    assert mp.default_key_env("gemini") == "GEMINI_API_KEY"
    store = mp.ProfileStore()
    p = mp.add_profile(
        store, name="g", provider="gemini", model_id="gemini-3.6-flash",
        input_price=0.3, output_price=2.5,
    )
    assert p.key_env == "GEMINI_API_KEY"


def test_gemini_prequalified_id_left_verbatim() -> None:
    p = mp.ModelProfile(name="g", provider="gemini", model_id="gemini/gemini-3.6-flash")
    assert p.litellm_id() == "gemini/gemini-3.6-flash"


def test_openrouter_model_id_is_preserved_and_idempotent() -> None:
    # OpenRouter ids carry two slashes and a ":free" suffix; they must pass through untouched
    # so a free-tier model is usable without a paid key.
    from reconrelate.llm_orchestration.relationship_engine import _litellm_model_id, is_cloud_model

    mid = "openrouter/meta-llama/llama-3.3-70b-instruct:free"
    p = mp.ModelProfile(name="or", provider="custom", model_id=mid)
    assert p.litellm_id() == mid
    assert _litellm_model_id(mid) == mid
    assert is_cloud_model(mid) is True
