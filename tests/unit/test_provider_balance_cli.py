import json

from reconrelate.cli.app import main
from reconrelate.core.provider_quota import ProviderQuotaSnapshot
from reconrelate.data_gathering.whoxy_reverse_whois_provider import WhoxyReverseWhoisProvider


def test_balance_requires_approval_before_provider_construction(monkeypatch, capsys) -> None:  # noqa: ANN001
    monkeypatch.setenv("WHOXY_API_KEY", "TEST-KEY")
    constructed = False

    def forbidden_init(self, api_key=None):  # noqa: ANN001, ARG001
        nonlocal constructed
        constructed = True

    monkeypatch.setattr(WhoxyReverseWhoisProvider, "__init__", forbidden_init)
    assert main(["providers", "balance", "--provider", "whoxy", "--max-billable-units", "1"]) == 2
    assert constructed is False
    assert "--approve-paid" in capsys.readouterr().err


def test_balance_requires_one_unit_before_provider_construction(monkeypatch, capsys) -> None:  # noqa: ANN001
    monkeypatch.setenv("WHOXY_API_KEY", "TEST-KEY")
    constructed = False

    def forbidden_init(self, api_key=None):  # noqa: ANN001, ARG001
        nonlocal constructed
        constructed = True

    monkeypatch.setattr(WhoxyReverseWhoisProvider, "__init__", forbidden_init)
    args = ["providers", "balance", "--provider", "whoxy", "--approve-paid",
            "--max-billable-units", "0.5"]
    assert main(args) == 2
    assert constructed is False
    assert "at least 1" in capsys.readouterr().err


def test_balance_requires_key_before_provider_construction(monkeypatch, capsys) -> None:  # noqa: ANN001
    monkeypatch.delenv("WHOXY_API_KEY", raising=False)
    constructed = False

    def forbidden_init(self, api_key=None):  # noqa: ANN001, ARG001
        nonlocal constructed
        constructed = True

    monkeypatch.setattr(WhoxyReverseWhoisProvider, "__init__", forbidden_init)
    args = ["providers", "balance", "--provider", "whoxy", "--approve-paid",
            "--max-billable-units", "1"]
    assert main(args) == 2
    assert constructed is False
    assert "WHOXY_API_KEY" in capsys.readouterr().err


def test_balance_success_is_bounded_and_machine_readable(monkeypatch, capsys) -> None:  # noqa: ANN001
    monkeypatch.setenv("WHOXY_API_KEY", "TEST-KEY")
    monkeypatch.delenv("RECONRELATE_DISABLE_PROVIDERS", raising=False)

    async def fake_balance(self):  # noqa: ANN001
        return ProviderQuotaSnapshot(
            provider="whoxy", capability="reverse_whois", unit="credit", remaining=42,
            authoritative=True, billing_effect="unknown", checked_at="2026-08-14T00:00:00+00:00",
        )

    monkeypatch.setattr(WhoxyReverseWhoisProvider, "balance", fake_balance)
    args = ["providers", "balance", "--provider", "whoxy", "--approve-paid",
            "--max-billable-units", "1", "--json"]
    assert main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["remaining"] == 42
    assert payload["reserved_billable_units"] == 1.0
    assert payload["request_attempts"] == 1
    assert payload["billing_effect"] == "unknown"
