import argparse

from reconrelate.cli.app import _build_parser
from reconrelate.cli.shell import _ARG_HINTS, _build_help


def _parser_commands() -> set[str]:
    parser = _build_parser()
    subparsers = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    return {choice.dest for choice in subparsers._choices_actions}


def test_shell_help_lists_every_real_command() -> None:
    # The original hand-written help silently omitted clusters, acquisitions, history and
    # domains — all working commands. Generating it from the parser makes that impossible,
    # and this test fails loudly if the generation ever regresses.
    help_text = _build_help()
    missing = [name for name in _parser_commands() if f"/{name}" not in help_text]
    assert not missing, f"commands missing from /help: {missing}"


def test_shell_help_includes_the_previously_missing_commands() -> None:
    help_text = _build_help()
    for name in ("clusters", "acquisitions", "history", "domains"):
        assert f"/{name}" in help_text


def test_shell_help_shows_a_description_for_each_command() -> None:
    # A command with no help= text would render as a bare name with no explanation.
    help_text = _build_help()
    for line in help_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("/") and not stripped.startswith(("/help", "/exit")):
            assert len(stripped.split(None, 1)) > 1, f"no description: {stripped!r}"


def test_arg_hints_do_not_name_commands_that_no_longer_exist() -> None:
    # Keeps the hint table honest if a command is ever renamed or removed.
    stale = set(_ARG_HINTS) - _parser_commands()
    assert not stale, f"_ARG_HINTS references unknown commands: {stale}"


def test_help_includes_exit_and_bare_domain_instructions() -> None:
    help_text = _build_help()
    assert "/exit" in help_text
    assert "Type a domain" in help_text
