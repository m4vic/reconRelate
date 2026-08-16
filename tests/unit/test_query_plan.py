from reconrelate.config.settings import Settings
from reconrelate.core.query_plan import build_query_plan, render_query_plan
from reconrelate.data_gathering.registry import PAID, ProviderInfo, ProviderRegistry
from reconrelate.core.factory import _pick
from reconrelate.core.provider_data_policy import WHOXY_DATA_POLICY


def _registry() -> ProviderRegistry:
    registry = ProviderRegistry()

    def must_not_instantiate():
        raise AssertionError("offline planning instantiated a provider")

    registry.register(ProviderInfo(
        "whois", "free-whois", must_not_instantiate,
        max_requests_per_attempt=2,
    ))
    registry.register(ProviderInfo(
        "basic_info", "html", must_not_instantiate,
        max_requests_per_attempt=3,
    ))
    registry.register(ProviderInfo(
        "reverse_whois", "paid-reverse", must_not_instantiate,
        tier=PAID, requires_env=("TEST_PAID_KEY",), billable=True,
        max_requests_per_attempt=1,
        data_policy=WHOXY_DATA_POLICY,
    ))
    return registry


def test_free_plan_is_manifest_only_and_excludes_configured_paid(monkeypatch) -> None:
    monkeypatch.setenv("TEST_PAID_KEY", "configured")
    settings = Settings.from_env()
    settings.provider_tier = "free"
    settings.global_max_nodes = 2
    settings.pivot_top_k = 2
    settings.max_domains_per_identifier = 3
    plan = build_query_plan(settings, _registry())
    assert plan.selected_providers == ("whois:free-whois", "basic_info:html")
    assert plan.policy_excluded_providers == ("reverse_whois:paid-reverse",)
    assert plan.worst_case_billable_units == 0
    assert plan.worst_case_logical_calls == 4
    assert plan.steps[0].source_families == ("unclassified",)
    assert "Network calls performed: 0" in render_query_plan(plan)
    assert "families=unclassified" in render_query_plan(plan)


def test_byok_plan_is_approval_gated_then_estimates_retry_worst_case(monkeypatch) -> None:
    monkeypatch.setenv("TEST_PAID_KEY", "configured")
    settings = Settings.from_env()
    settings.provider_tier = "byok"
    settings.global_max_nodes = 2
    settings.pivot_top_k = 2
    settings.max_domains_per_identifier = 1
    settings.retry_count = 1
    gated = build_query_plan(settings, _registry(), paid_approved=False)
    assert gated.approval_gated_providers == ("reverse_whois:paid-reverse",)
    assert gated.worst_case_billable_units == 0
    approved = build_query_plan(settings, _registry(), paid_approved=True)
    assert "reverse_whois:paid-reverse" in approved.selected_providers
    assert approved.worst_case_billable_units == 8  # 2 calls/domain * 2 domains * 2 attempts


def test_plan_json_declares_zero_execution() -> None:
    value = build_query_plan(Settings.from_env(), _registry()).to_dict()
    assert value["network_calls_performed"] == 0
    assert value["billable_calls_performed"] == 0


def test_runtime_free_policy_falls_back_from_available_paid_provider(monkeypatch) -> None:
    monkeypatch.setenv("TEST_PAID_KEY", "configured")
    registry = ProviderRegistry()
    class Provider:
        pass

    registry.register(ProviderInfo("reverse_whois", "free", Provider))
    registry.register(ProviderInfo(
        "reverse_whois", "paid", Provider, tier=PAID,
        requires_env=("TEST_PAID_KEY",), billable=True,
        data_policy=WHOXY_DATA_POLICY,
    ))
    assert getattr(_pick(registry, "reverse_whois", allow_billable=False), "__reconrelate_provider__") == "free"
    assert getattr(_pick(registry, "reverse_whois", allow_billable=True), "__reconrelate_provider__") == "paid"
