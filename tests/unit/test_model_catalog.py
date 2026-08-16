import json

from reconrelate.cli.app import main
from reconrelate.config.settings import Settings
from reconrelate.llm_orchestration import model_catalog


def _local_settings(monkeypatch, model: str = "qwen2.5:7b-instruct") -> Settings:
    monkeypatch.setenv("LLM_MODEL", model)
    monkeypatch.delenv("RECONRELATE_LLM_ALLOW_CLOUD", raising=False)
    return Settings.from_env()


def test_catalog_does_not_recommend_unevaluated_models() -> None:
    payload = model_catalog.catalog_payload()
    assert payload["automatic_recommendation"] is None
    assert all(item["quality_status"] == "unevaluated" for item in payload["models"])


def test_local_doctor_reports_installed_model_ready(monkeypatch) -> None:
    settings = _local_settings(monkeypatch)
    monkeypatch.setattr(
        model_catalog, "_installed_ollama_models", lambda api_base, timeout: {"qwen2.5:7b-instruct"}
    )
    result = model_catalog.diagnose_model(settings)
    assert result.ready is True
    assert {check.name: check.status for check in result.checks}["model_installed"] == "ok"


def test_local_doctor_reports_missing_and_unreachable_models(monkeypatch) -> None:
    settings = _local_settings(monkeypatch)
    monkeypatch.setattr(model_catalog, "_installed_ollama_models", lambda api_base, timeout: set())
    assert model_catalog.diagnose_model(settings).ready is False

    def unreachable(api_base, timeout):  # noqa: ANN001
        raise OSError("offline")

    monkeypatch.setattr(model_catalog, "_installed_ollama_models", unreachable)
    result = model_catalog.diagnose_model(settings)
    assert result.ready is False
    assert {check.name: check.status for check in result.checks}["ollama"] == "error"


def test_cloud_doctor_checks_gate_and_key_without_calling_provider(monkeypatch) -> None:
    monkeypatch.setenv("LLM_MODEL", "gpt-5-mini")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings.from_env()
    result = model_catalog.diagnose_model(settings)
    statuses = {check.name: check.status for check in result.checks}
    assert result.runtime == "cloud" and result.ready is False
    assert statuses["cloud_admin_gate"] == "error"
    assert statuses["credential"] == "error"
    assert statuses["price_envelope"] == "ok"


def test_doctor_checks_fast_model_and_reports_mixed_runtime(monkeypatch) -> None:
    monkeypatch.setenv("LLM_MODEL", "qwen2.5:7b-instruct")
    monkeypatch.setenv("FAST_LLM_MODEL", "gpt-5-mini")
    monkeypatch.setenv("RECONRELATE_LLM_ALLOW_CLOUD", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "present-for-test")
    monkeypatch.setattr(
        model_catalog, "_installed_ollama_models", lambda api_base, timeout: {"qwen2.5:7b-instruct"}
    )
    result = model_catalog.diagnose_model(Settings.from_env())
    assert result.runtime == "mixed" and result.ready is True
    statuses = {check.name: check.status for check in result.checks}
    assert statuses["credential:fast"] == "ok"
    assert statuses["model_installed"] == "ok"


def test_default_timeouts_allow_model_to_finish_before_domain_deadline(monkeypatch) -> None:
    monkeypatch.delenv("LLM_TIMEOUT_SEC", raising=False)
    monkeypatch.delenv("PER_DOMAIN_TIMEOUT_SEC", raising=False)
    settings = Settings.from_env()
    assert settings.llm_timeout_sec == 120
    assert settings.per_domain_timeout_sec == 180


def test_models_list_cli_json_is_machine_readable(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("RECONRELATE_CONFIG_PATH", str(tmp_path / "absent.json"))
    assert main(["models", "list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["catalog_version"] == model_catalog.MODEL_CATALOG_VERSION
    assert payload["automatic_recommendation"] is None
