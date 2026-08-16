from reconrelate.cli.app import _build_parser, _normalize_cli_argv, main


def test_bare_domain_uses_the_run_command() -> None:
    args = _build_parser().parse_args(_normalize_cli_argv(["example.com"]))

    assert args.command == "run"
    assert args.domain == "example.com"


def test_quick_shortcut_sets_safe_first_look_defaults() -> None:
    args = _build_parser().parse_args(_normalize_cli_argv(["quick", "example.com"]))

    assert args.command == "run"
    assert args.run_mode == "quick"
    assert args.budget == "low"
    assert args.domain == "example.com"


def test_slash_shortcuts_map_to_existing_options() -> None:
    args = _build_parser().parse_args(
        _normalize_cli_argv(["/quick", "example.com", "/depth:2", "/json"])
    )

    assert args.command == "run"
    assert args.run_mode == "quick"
    assert args.budget == "low"
    assert args.max_depth == 2
    assert args.as_json is True


def test_help_command_becomes_standard_argparse_help() -> None:
    assert _normalize_cli_argv(["help", "run"]) == ["run", "--help"]


def test_byok_run_requires_explicit_paid_approval(capsys) -> None:
    assert main(["run", "example.com", "--profile", "byok"]) == 2

    assert "requires --approve-paid" in capsys.readouterr().err


def test_byok_run_requires_a_positive_billable_ceiling(capsys) -> None:
    assert (
        main(
            [
                "run",
                "example.com",
                "--profile",
                "byok",
                "--approve-paid",
            ]
        )
        == 2
    )

    assert "positive --max-billable-units" in capsys.readouterr().err


def test_cloud_model_requires_per_run_approval(monkeypatch, capsys) -> None:
    monkeypatch.setenv("RECONRELATE_LLM_ALLOW_CLOUD", "true")

    assert main(["run", "example.com", "--model", "gpt-5"]) == 2
    assert "requires --approve-cloud" in capsys.readouterr().err


def test_approved_cloud_model_requires_positive_token_ceiling(monkeypatch, capsys) -> None:
    monkeypatch.setenv("RECONRELATE_LLM_ALLOW_CLOUD", "true")

    assert main(["run", "example.com", "--model", "gpt-5", "--approve-cloud"]) == 2
    assert "positive --max-cloud-tokens" in capsys.readouterr().err


def test_approved_cloud_model_requires_positive_dollar_ceiling(monkeypatch, capsys) -> None:
    monkeypatch.setenv("RECONRELATE_LLM_ALLOW_CLOUD", "true")

    assert main([
        "run", "example.com", "--model", "gpt-5-mini", "--approve-cloud",
        "--max-cloud-tokens", "1000",
    ]) == 2
    assert "positive --max-cloud-cost-usd" in capsys.readouterr().err


def test_local_model_rejects_irrelevant_cloud_approval(capsys) -> None:
    assert main(["run", "example.com", "--model", "ollama/test", "--approve-cloud"]) == 2
    assert "valid only for a configured cloud model" in capsys.readouterr().err


def test_cloud_fast_model_cannot_bypass_run_approval(monkeypatch, capsys) -> None:
    monkeypatch.setenv("RECONRELATE_LLM_ALLOW_CLOUD", "true")
    assert main([
        "run", "example.com", "--model", "ollama/local", "--fast-model", "gpt-5-mini"
    ]) == 2
    assert "cloud primary/fast model requires --approve-cloud" in capsys.readouterr().err
