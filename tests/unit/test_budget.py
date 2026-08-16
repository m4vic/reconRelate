import pytest

from reconrelate.config.settings import Settings

_KNOBS = ("DEFAULT_MAX_DEPTH", "GLOBAL_MAX_NODES", "PIVOT_TOP_K", "MAX_DOMAINS_PER_IDENTIFIER", "RECONRELATE_BUDGET")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in _KNOBS:
        monkeypatch.delenv(k, raising=False)


def test_no_budget_keeps_classic_defaults() -> None:
    s = Settings.from_env()
    assert s.default_max_depth == -1
    assert s.global_max_nodes == 500


def test_low_budget_is_a_shallow_scout() -> None:
    s = Settings.from_env(budget_cli="low")
    assert s.default_max_depth == 1
    assert s.global_max_nodes == 50
    assert s.pivot_top_k == 3


def test_max_budget_is_exhaustive() -> None:
    s = Settings.from_env(budget_cli="max")
    assert s.default_max_depth == -1        # unlimited depth
    assert s.global_max_nodes == 2000
    assert s.max_domains_per_identifier == 10


def test_specific_env_overrides_budget(monkeypatch) -> None:
    monkeypatch.setenv("GLOBAL_MAX_NODES", "999")
    s = Settings.from_env(budget_cli="low")
    assert s.global_max_nodes == 999        # explicit env wins over the preset
    assert s.default_max_depth == 1         # other preset values still apply


def test_budget_from_env_var(monkeypatch) -> None:
    monkeypatch.setenv("RECONRELATE_BUDGET", "medium")
    s = Settings.from_env()
    assert s.default_max_depth == 2
    assert s.global_max_nodes == 250


def test_unknown_budget_falls_back_to_defaults() -> None:
    s = Settings.from_env(budget_cli="banana")
    assert s.global_max_nodes == 500
