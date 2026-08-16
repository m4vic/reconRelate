import pytest

from reconrelate.core.provider_data_policy import ProviderDataPolicy
from reconrelate.data_gathering.registry import PAID, ProviderInfo


@pytest.mark.parametrize("kwargs", [
    {"raw_retention": "raw"},
    {"normalized_retention": "forever"},
    {"export_scope": "everything"},
    {"version": ""},
    {"normalized_retention": "run", "cross_run_cache": True},
])
def test_invalid_provider_data_policies_fail_closed(kwargs) -> None:  # noqa: ANN001
    with pytest.raises(ValueError):
        ProviderDataPolicy(**kwargs)


def test_paid_provider_registration_requires_explicit_data_policy() -> None:
    with pytest.raises(ValueError, match="explicit data policy"):
        ProviderInfo("reverse_whois", "new-paid", object, tier=PAID, billable=True)
