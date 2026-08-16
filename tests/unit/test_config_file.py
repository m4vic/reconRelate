import pytest

from reconrelate.config import config_file as cf
from reconrelate.config.settings import Settings
from reconrelate.core.factory import _pick, _pick_all
from reconrelate.data_gathering.registry import default_registry
from reconrelate.data_gathering.reverse_whois_provider import ReverseWhoisProvider
from reconrelate.data_gathering.whoxy_reverse_whois_provider import WhoxyReverseWhoisProvider
from reconrelate.data_gathering.rdap_provider import RdapProvider
from reconrelate.data_gathering.whois_provider import WhoisProvider


@pytest.fixture()
def cfg_path(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    monkeypatch.setenv("RECONRELATE_CONFIG_PATH", str(p))
    return p


def test_alias_set_roundtrips_to_env_name(cfg_path) -> None:
    env_name = cf.set_value("model", "qwen3.5:9b")
    assert env_name == "LLM_MODEL"
    assert cf.load_config() == {"LLM_MODEL": "qwen3.5:9b"}


def test_key_and_source_namespaces(cfg_path) -> None:
    assert cf.set_value("key.WHOXY_API_KEY", "sk-123456") == "WHOXY_API_KEY"
    assert cf.set_value("source.reverse_whois", "whoxy") == "RECONRELATE_SOURCE_REVERSE_WHOIS"
    stored = cf.load_config()
    assert stored["WHOXY_API_KEY"] == "sk-123456"
    assert stored["RECONRELATE_SOURCE_REVERSE_WHOIS"] == "whoxy"


def test_unknown_key_raises(cfg_path) -> None:
    with pytest.raises(ValueError):
        cf.set_value("nonsense_key", "x")


def test_unset_removes_value(cfg_path) -> None:
    cf.set_value("model", "x")
    cf.unset_value("model")
    assert "LLM_MODEL" not in cf.load_config()


def test_secret_detection_and_masking() -> None:
    assert cf.is_secret("WHOXY_API_KEY")
    assert not cf.is_secret("LLM_MODEL")
    assert cf.mask("sk-abcdefgh").endswith("efgh")
    assert "abcd" not in cf.mask("sk-abcdefgh")  # only the last 4 chars leak
    assert cf.mask("ab") == "****"


def test_apply_config_to_env_real_env_wins() -> None:
    env = {"LLM_MODEL": "from-env"}
    cf.apply_config_to_env(cfg={"LLM_MODEL": "from-file", "OLLAMA_API_BASE": "http://x"}, env=env)
    assert env["LLM_MODEL"] == "from-env"        # real env not overwritten
    assert env["OLLAMA_API_BASE"] == "http://x"  # file fills what env lacks


def test_default_is_free_and_local(cfg_path, monkeypatch) -> None:
    # No config file, no keys -> Settings resolves to local defaults, no reverse-whois key.
    for var in ("LLM_MODEL", "WHOXY_API_KEY", "RECONRELATE_LLM_ALLOW_CLOUD"):
        monkeypatch.delenv(var, raising=False)
    cf.apply_config_to_env()  # empty file
    settings = Settings.from_env()
    assert settings.llm_model == ""  # factory then defaults this to local qwen3.5:9b
    assert default_registry().get("reverse_whois").__class__ is ReverseWhoisProvider


def test_source_pin_forces_free_even_with_key(monkeypatch) -> None:
    monkeypatch.setenv("WHOXY_API_KEY", "sk-present")
    reg = default_registry()
    # Auto would prefer paid Whoxy...
    assert isinstance(reg.get("reverse_whois"), WhoxyReverseWhoisProvider)
    # ...but an explicit pin to the free source wins.
    monkeypatch.setenv("RECONRELATE_SOURCE_REVERSE_WHOIS", "duckduckgo")
    assert isinstance(_pick(reg, "reverse_whois"), ReverseWhoisProvider)


def test_unavailable_pin_falls_back_to_auto(monkeypatch) -> None:
    monkeypatch.delenv("WHOXY_API_KEY", raising=False)
    monkeypatch.setenv("RECONRELATE_SOURCE_REVERSE_WHOIS", "whoxy")  # unavailable (no key)
    # Falls back to the free provider instead of returning None / crashing.
    assert isinstance(_pick(default_registry(), "reverse_whois"), ReverseWhoisProvider)


def test_registration_auto_is_rdap_first_and_pin_selects_exactly_one(monkeypatch) -> None:
    monkeypatch.delenv("RECONRELATE_SOURCE_WHOIS", raising=False)
    providers = _pick_all(default_registry(), "whois")
    assert [type(item) for item in providers] == [RdapProvider, WhoisProvider]
    monkeypatch.setenv("RECONRELATE_SOURCE_WHOIS", "python-whois")
    pinned = _pick_all(default_registry(), "whois")
    assert len(pinned) == 1
    assert isinstance(pinned[0], WhoisProvider)


def test_render_show_masks_secrets(cfg_path, monkeypatch) -> None:
    monkeypatch.setenv("WHOXY_API_KEY", "sk-supersecret")
    out = cf.render_show(Settings.from_env())
    assert "sk-supersecret" not in out
    assert "WHOXY_API_KEY" in out
