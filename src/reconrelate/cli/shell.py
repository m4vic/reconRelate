"""Interactive REPL.

Bare `reconrelate` (or `python run.py` with no domain) launches this when stdin is a real
terminal — scripted/piped invocations are untouched (see the isatty gate at each call site).

Slash commands dispatch to the exact same argparse commands the one-shot CLI already has —
`cli.app.main([...])` — so there is no second copy of argument parsing or command logic to
keep in sync. A bare domain (no leading `/`) is shorthand for a safe quick scout.
"""

from __future__ import annotations

import argparse
import shlex

try:
    from rich.console import Console
    _console: "Console | None" = Console()
except Exception:  # rich not installed / unusable terminal — fall back to plain output
    _console = None

BANNER = r"""
 ____                        ____      _       _
|  _ \ ___  ___ ___  _ __   |  _ \ ___| | __ _| |_ ___
| |_) / _ \/ __/ _ \| '_ \  | |_) / _ \ |/ _` | __/ _ \
|  _ <  __/ (_| (_) | | | | |  _ <  __/ | (_| | ||  __/
|_| \_\___|\___\___/|_| |_| |_| \_\___|_|\__,_|\__\___|
"""

# Extra usage hints per command, merged into the generated help below. Only the argument
# shape lives here — the command list itself and every description are read from the real
# argparse parser, so a new subcommand can never be missing from `/help` again.
_ARG_HINTS = {
    "run": "<domain> [flags...]",
    "plan": "<domain> [flags...]",
    "tree": "<run_id>",
    "report": "<run_id>",
    "export": "<run_id> --out <dir>",
    "clusters": "<run_id>",
    "domains": "<run_id>",
    "acquisitions": "<org name>",
    "history": "<domain>",
    "eval": "<graph.json> --case <case.json>",
    "model": "list|add|use|show|remove",
    "models": "list|doctor|benchmark",
    "providers": "[doctor]",
    "config": "show|set|unset",
    "db": "check|backup|restore|retention",
}


def _build_help() -> str:
    """Generate the slash-command list from the real parser, so the two cannot drift apart."""
    from reconrelate.cli.app import _build_parser

    lines = [
        "Type a domain to scan it (quick scout, depth 1), or use a slash command:",
        "",
    ]
    parser = _build_parser()
    subparsers = next(
        (a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None
    )
    if subparsers is not None:
        seen: set[str] = set()
        for choice in subparsers._choices_actions:
            name = choice.dest
            if name in seen:
                continue
            seen.add(name)
            invocation = f"/{name} {_ARG_HINTS.get(name, '')}".rstrip()
            lines.append(f"  {invocation:<38} {choice.help or ''}".rstrip())
    lines += [
        "",
        f"  {'/help':<38} this message",
        f"  {'/exit, /quit':<38} leave",
    ]
    return "\n".join(lines)


def _print(text: str, *, style: str | None = None) -> None:
    if _console is not None:
        _console.print(text, style=style, highlight=False)
    else:
        print(text)


def _print_banner() -> None:
    _print(BANNER, style="bold red")
    _print("  free-first domain & acquisition relationship mapping\n", style="red")


def _looks_like_domain(token: str) -> bool:
    return "." in token and " " not in token and not token.startswith("/") and not token.startswith("-")


def run_shell() -> int:
    """Run the interactive REPL until the user exits; returns a process exit code (0 normally,
    or the exit code of the last command run if the caller wants to propagate it — currently
    always 0, since the loop below only ever `return`s 0).

    Callers are expected to have already checked `sys.stdin.isatty()`; this function does not
    check it itself, so calling it over a pipe will block on `input()` waiting for EOF rather
    than falling back to non-interactive behavior — that fallback lives at the call sites
    (`cli/app.py`, `run.py`), not here.
    """
    from reconrelate.cli import app as app_module

    # The shell already showed the banner once for this session — _handle_run's own
    # print_banner() would otherwise re-print it before every /run and every bare-domain
    # dispatch, which reads as a confusing duplicate. Scoped to this in-process shell only (and
    # restored in the finally below) so a plain one-shot `reconrelate run ...` invocation in the
    # same process — e.g. a test — still gets the real print_banner.
    real_print_banner = app_module.print_banner
    app_module.print_banner = lambda: None
    cli_main = app_module.main
    try:
        return _loop(cli_main)
    finally:
        app_module.print_banner = real_print_banner


def _loop(cli_main) -> int:
    _print_banner()
    _print("Type a domain to scan, [bold]/help[/bold] for commands, [bold]/exit[/bold] to leave.\n"
           if _console is not None else "Type a domain to scan, /help for commands, /exit to leave.\n")

    while True:
        try:
            # Print the styled prompt via rich, then read with the plain builtin input() —
            # rich's own Console.input() intercepts line editing itself, which on some Windows
            # terminals renders backspace incorrectly. Builtin input() defers to the terminal's
            # native line editing, which is always reliable.
            if _console is not None:
                _console.print("recon>", style="bold red", end=" ")
            else:
                print("recon>", end=" ")
            line = input()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        line = line.strip()
        if not line:
            continue
        if line in ("/exit", "/quit", "exit", "quit"):
            return 0
        if line in ("/help", "help", "?"):
            _print(_build_help())
            continue

        try:
            tokens = shlex.split(line)
        except ValueError as exc:
            _print(f"Could not parse that line: {exc}", style="red")
            continue
        if not tokens:
            continue

        if tokens[0].startswith("/"):
            tokens[0] = tokens[0][1:]
            argv = tokens
        elif len(tokens) == 1 and _looks_like_domain(tokens[0]):
            argv = ["run", tokens[0], "--mode", "quick", "--max-depth", "1", "--budget", "low"]
            _print(
                f"Quick scout: {tokens[0]}  - deterministic only, no model calls (quick mode "
                f"always skips the model, even if one is configured with /model use). "
                f"For a model-assisted map: /run {tokens[0]} --mode deep --acquisitions",
                style="dim",
            )
        else:
            # Anything else (e.g. "run acme.com --acquisitions" typed without a slash) goes
            # through unchanged — cli_main's own argv normalization already handles it.
            argv = tokens

        try:
            code = cli_main(argv)
            if code != 0:
                _print(f"(exit code {code})", style="dim red")
        except SystemExit:
            pass
        except Exception as exc:  # keep the shell alive on an unexpected error
            _print(f"Error: {exc}", style="red")
