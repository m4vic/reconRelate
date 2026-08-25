from __future__ import annotations

import argparse
import json
import logging
import math
import os
import stat
import sys
from dataclasses import asdict
from pathlib import Path

from reconrelate import __version__
from reconrelate.config.settings import Settings
from reconrelate.core.errors import SecurityError
from reconrelate.output.artifacts import write_run_bundle
from reconrelate.output.renderers import (
    render_ascii_tree,
    render_graph_json,
    render_markdown_report,
)
from reconrelate.core.factory import build_runtime


_COMMAND_ALIASES = {
    "/?": ["--help"],
    "/help": ["--help"],
    "/h": ["--help"],
    "/run": ["run"],
    "/scan": ["run"],
    "/quick": ["quick"],
    "/deep": ["deep"],
    "/tree": ["tree"],
    "/report": ["report"],
    "/export": ["export"],
    "/providers": ["providers"],
    "/models": ["models"],
    "/model": ["model"],
    "/config": ["config"],
    "/clusters": ["clusters"],
    "/domains": ["domains"],
    "/acquisitions": ["acquisitions"],
    "/history": ["history"],
    "/plan": ["plan"],
    "/eval": ["eval"],
    "/db": ["db"],
}
_FLAG_ALIASES = {
    "/json": ["--json"],
    "/resume": ["--resume"],
    "/refresh": ["--refresh"],
    "/acquisitions": ["--acquisitions"],
    "/history": ["--history"],
    "/no-save": ["--no-save"],
}

class ReconRelateArgumentParser(argparse.ArgumentParser):
    """Show a useful recovery path when a command is mistyped."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"reconrelate: error: {message}\nTry 'reconrelate help' for examples.\n")


def _normalize_cli_argv(argv: list[str]) -> list[str]:
    """Accept discoverable shortcuts without changing the stable command API."""
    if not argv:
        return argv

    normalized: list[str] = []
    for index, token in enumerate(argv):
        lowered = token.lower()
        if index == 0 and lowered in _COMMAND_ALIASES:
            normalized.extend(_COMMAND_ALIASES[lowered])
        elif lowered in _FLAG_ALIASES:
            normalized.extend(_FLAG_ALIASES[lowered])
        elif lowered in {"/help", "/h", "/?"}:
            normalized.append("--help")
        elif lowered.startswith("/depth:"):
            normalized.extend(["--max-depth", token.split(":", 1)[1]])
        elif lowered.startswith("/budget:"):
            normalized.extend(["--budget", token.split(":", 1)[1]])
        elif lowered.startswith("/mode:"):
            normalized.extend(["--mode", token.split(":", 1)[1]])
        else:
            normalized.append(token)

    if normalized[0] == "help":
        return [*normalized[1:], "--help"] if len(normalized) > 1 else ["--help"]
    if normalized[0] == "quick":
        return ["run", "--mode", "quick", "--budget", "low", *normalized[1:]]
    if normalized[0] == "deep":
        return ["run", "--mode", "deep", *normalized[1:]]
    if normalized[0] not in {"run", "plan", "tree", "report", "export", "providers", "models", "model", "clusters", "acquisitions", "history", "domains", "config", "eval", "db", "--help", "-h", "--version"} and not normalized[0].startswith("-"):
        return ["run", *normalized]
    return normalized


def configure_logging(verbose: bool = False) -> None:
    """Quiet by default. The CLI prints its own clean progress, status, and tree; Python and
    third-party logging is developer diagnostics, surfaced only with --verbose."""
    level = logging.INFO if verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s", force=True)
    # These libraries log at INFO and flood the terminal (e.g. LiteLLM prints every completion).
    # Keep them silent unless the user explicitly asks for diagnostics.
    for noisy in ("LiteLLM", "litellm", "httpx", "httpcore", "aiohttp"):
        logging.getLogger(noisy).setLevel(logging.INFO if verbose else logging.ERROR)


BANNER = r"""
 ____                        ____      _       _
|  _ \ ___  ___ ___  _ __   |  _ \ ___| | __ _| |_ ___
| |_) / _ \/ __/ _ \| '_ \  | |_) / _ \ |/ _` | __/ _ \
|  _ <  __/ (_| (_) | | | | |  _ <  __/ | (_| | ||  __/
|_| \_\___|\___\___/|_| |_| |_| \_\___|_|\__,_|\__\___|

  free-first domain & acquisition relationship mapping
"""


def print_banner() -> None:
    sys.stderr.write(BANNER + "\n")

def _build_parser() -> argparse.ArgumentParser:
    parser = ReconRelateArgumentParser(
        prog="reconrelate",
        description="Map relationships between an authorized domain and its infrastructure.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  reconrelate example.com              Map a domain using the default deep preset
  reconrelate quick example.com        Fast, bounded first look (low budget)
  reconrelate run example.com --budget medium --acquisitions
  reconrelate report <run-id>          Read a completed run
  reconrelate eval graph.json --case case.json
  reconrelate /quick example.com /json Windows-friendly slash form

Start here: reconrelate help | reconrelate providers | reconrelate config show
Model setup: reconrelate models doctor | reconrelate models list
Database safety: reconrelate db check | reconrelate db backup
Use only against domains you are authorized to assess.""",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Start a recon run")
    run_parser.add_argument("domain", help="Root domain")
    run_parser.add_argument(
        "--max-depth",
        type=int,
        default=None,
        metavar="N",
        help="Max BFS depth (>=0). Omit to use DEFAULT_MAX_DEPTH from env (-1 = unlimited until queue/global limits).",
    )
    run_parser.add_argument("--pivot-top-k", type=int, default=None, help="Max pivots per domain")
    run_parser.add_argument(
        "--mode",
        choices=("quick", "deep"),
        default=None,
        dest="run_mode",
        help="Run preset: quick (fast spot-check) or deep (thorough mapping, default)",
    )
    run_parser.add_argument(
        "--budget",
        choices=("low", "medium", "max"),
        default=None,
        help="Crawl-size tier: low (~50 domains, depth 1, a fast scout), medium (~250, depth 2), max (exhaustive)",
    )
    run_parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="LLM model to use (overrides config/env settings)",
    )
    run_parser.add_argument(
        "--no-save",
        action="store_true",
        help="Skip writing tree/graph/report files after run (DB is always updated)",
    )
    run_parser.add_argument(
        "--artifacts-dir",
        default=None,
        help="Directory for auto-saved artifacts (default: RECONRELATE_ARTIFACTS_DIR or ./artifacts)",
    )
    run_parser.add_argument(
        "--fast-model",
        type=str,
        default=None,
        dest="fast_model",
        help="Lightweight LLM for quick tasks (e.g. qwen2.5:1.5b)",
    )
    run_parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume the last interrupted run for this domain instead of starting fresh",
    )
    run_parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force a fresh scrape, ignoring the cross-run cache (re-map already-known domains)",
    )
    run_parser.add_argument(
        "--acquisitions",
        action="store_true",
        dest="expand_acquisitions",
        help="Expand org pivots via Wikidata parent/subsidiary/ownership (map acquired siblings)",
    )
    run_parser.add_argument(
        "--history", action="store_true", dest="historical_web",
        help="Collect bounded timestamped Wayback root-page evidence (extra network calls)",
    )
    run_parser.add_argument(
        "--profile", choices=("free", "byok"), default=None,
        help="Provider policy: free (default, never billable) or byok (explicit approval required)",
    )
    run_parser.add_argument(
        "--approve-paid", action="store_true",
        help="Approve configured billable providers for this run only; requires --profile byok",
    )
    run_parser.add_argument(
        "--max-provider-calls", type=int, default=None,
        help="Hard run-wide logical provider-call ceiling",
    )
    run_parser.add_argument(
        "--max-billable-units", type=float, default=None,
        help="Hard run-wide worst-case billable-unit ceiling",
    )
    run_parser.add_argument(
        "--approve-cloud", action="store_true",
        help="Approve the configured cloud model for this run; allow_cloud must also be enabled",
    )
    run_parser.add_argument("--max-model-calls", type=int, default=None, help="Hard model-call ceiling")
    run_parser.add_argument("--max-model-input-tokens", type=int, default=None)
    run_parser.add_argument("--max-model-output-tokens", type=int, default=None)
    run_parser.add_argument(
        "--max-cloud-tokens", type=int, default=None,
        help="Hard conservative input+output token ceiling for an approved cloud model",
    )
    run_parser.add_argument(
        "--max-cloud-cost-usd", type=float, default=None,
        help="Hard pre-call cloud cost envelope in USD (catalog-priced, rounded up)",
    )
    run_parser.add_argument("--json", action="store_true", dest="as_json", help="Output JSON")
    run_parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed diagnostic logs")

    plan_parser = subparsers.add_parser(
        "plan", help="Show an offline provider/cost preflight plan (zero network calls)"
    )
    plan_parser.add_argument("domain", help="Authorized root domain")
    plan_parser.add_argument("--mode", choices=("quick", "deep"), default=None, dest="run_mode")
    plan_parser.add_argument("--budget", choices=("low", "medium", "max"), default=None)
    plan_parser.add_argument("--profile", choices=("free", "byok"), default=None)
    plan_parser.add_argument("--approve-paid", action="store_true")
    plan_parser.add_argument("--acquisitions", action="store_true", dest="expand_acquisitions")
    plan_parser.add_argument("--history", action="store_true", dest="historical_web")
    plan_parser.add_argument("--max-provider-calls", type=int, default=None)
    plan_parser.add_argument("--max-billable-units", type=float, default=None)
    plan_parser.add_argument("--json", action="store_true", dest="as_json")

    tree_parser = subparsers.add_parser("tree", help="Render run tree")
    tree_parser.add_argument("run_id", help="Run ID")
    tree_parser.add_argument("--format", choices=["ascii", "json"], default="ascii")

    report_parser = subparsers.add_parser("report", help="Render run report")
    report_parser.add_argument("run_id", help="Run ID")
    report_parser.add_argument("--format", choices=["md", "json"], default="md")

    export_parser = subparsers.add_parser(
        "export", help="Export run artifacts with provider data-use policies enforced"
    )
    export_parser.add_argument("run_id", help="Run ID")
    export_parser.add_argument("--out", default="artifacts", help="Output directory")

    providers_parser = subparsers.add_parser("providers", help="List or diagnose recon data sources")
    providers_parser.add_argument(
        "providers_action", nargs="?", choices=("list", "doctor", "balance", "value", "compare", "benchmark"), default="list",
        help="list, diagnose, explicitly check balance, report value, compare, or benchmark",
    )
    providers_parser.add_argument("--json", action="store_true", dest="as_json")
    providers_parser.add_argument(
        "--live", action="store_true",
        help="Probe supported free providers over the network (paid providers always skipped)",
    )
    providers_parser.add_argument(
        "--target", default=None,
        help="Authorized domain required with --live",
    )
    providers_parser.add_argument(
        "--run-id", default=None,
        help="Completed or partial run to analyze with `providers value`",
    )
    providers_parser.add_argument("--baseline", default=None, help="Free baseline graph for `providers compare`")
    providers_parser.add_argument("--candidate", default=None, help="Candidate graph for `providers compare`")
    providers_parser.add_argument("--case", default=None, dest="case_path", help="Evaluation case for `providers compare`")
    providers_parser.add_argument("--manifest", default=None, help="Versioned manifest for `providers benchmark`")
    providers_parser.add_argument(
        "--provider", choices=("whoxy",), default=None,
        help="Provider for an explicit account operation such as `providers balance`",
    )
    providers_parser.add_argument(
        "--approve-paid", action="store_true",
        help="Approve the potentially billable account request",
    )
    providers_parser.add_argument(
        "--max-billable-units", type=float, default=None,
        help="Hard ceiling for the explicit account request",
    )

    models_parser = subparsers.add_parser(
        "models", help="Inspect the versioned model catalog or diagnose model setup"
    )
    models_parser.add_argument(
        "models_action", nargs="?", choices=("list", "doctor", "benchmark"), default="list"
    )
    models_parser.add_argument("--json", action="store_true", dest="as_json")
    models_parser.add_argument("--manifest", default=None, help="Model benchmark manifest")
    models_parser.add_argument("--model", default=None, help="Model override for benchmark")
    models_parser.add_argument("--approve-cloud", action="store_true")
    models_parser.add_argument("--max-model-calls", type=int, default=None)
    models_parser.add_argument("--max-model-input-tokens", type=int, default=None)
    models_parser.add_argument("--max-model-output-tokens", type=int, default=None)
    models_parser.add_argument("--max-cloud-tokens", type=int, default=None)
    models_parser.add_argument("--max-cloud-cost-usd", type=float, default=None)

    model_parser = subparsers.add_parser(
        "model", help="Add, select, list, or remove named model profiles (local + cloud)"
    )
    model_sub = model_parser.add_subparsers(dest="model_command")
    add_p = model_sub.add_parser("add", help="Add or update a named model profile")
    add_p.add_argument("name", help="Profile name, e.g. local-fast, openai-mini")
    add_p.add_argument("model_id", help="Model id, e.g. qwen2.5:7b-instruct, gpt-5.6-luna, claude-...")
    add_p.add_argument(
        "--provider", choices=("ollama", "openai", "anthropic", "custom"), default=None,
        help="Defaults to a guess from the model id (ollama unless it looks like a cloud model)",
    )
    add_p.add_argument("--api-base", default="", help="Ollama daemon URL, or a custom endpoint URL")
    add_p.add_argument(
        "--key", default="", dest="key_env",
        help="Env var name holding the API key (default: OPENAI_API_KEY / ANTHROPIC_API_KEY)",
    )
    add_p.add_argument(
        "--key-value", default=None,
        help="Store this value under --key (or the provider default name) in the same step",
    )
    add_p.add_argument(
        "--input-price", type=float, default=None,
        help="USD per million input tokens (required for a cloud model with no built-in price)",
    )
    add_p.add_argument("--output-price", type=float, default=None, help="USD per million output tokens")

    use_p = model_sub.add_parser("use", help="Assign a profile to a role")
    use_p.add_argument("name", help="Profile name")
    use_p.add_argument(
        "--role", choices=("primary", "fast"), default="primary",
        help="primary: used standalone or on escalation. fast: tried first, cheaper.",
    )

    list_p = model_sub.add_parser("list", help="List configured profiles")
    list_p.add_argument("--json", action="store_true", dest="as_json")
    show_p = model_sub.add_parser("show", help="Show one profile")
    show_p.add_argument("name")
    show_p.add_argument("--json", action="store_true", dest="as_json")
    remove_p = model_sub.add_parser("remove", help="Remove a profile")
    remove_p.add_argument("name")

    clusters_parser = subparsers.add_parser(
        "clusters", help="Show same-operator clusters (domains sharing an identifier) for a run"
    )
    clusters_parser.add_argument("run_id", help="Run ID")
    clusters_parser.add_argument("--min-domains", type=int, default=2, help="Min domains per cluster")

    acq_parser = subparsers.add_parser(
        "acquisitions", help="List corporate relationships from free/selected sources"
    )
    acq_parser.add_argument("org", help="Organization or company name")
    acq_parser.add_argument("--max", type=int, default=20, dest="max_results", help="Max relations")
    acq_parser.add_argument(
        "--source", default="auto", help="Provider name, or auto to query every available source"
    )
    acq_parser.add_argument("--json", action="store_true", dest="as_json")

    history_parser = subparsers.add_parser(
        "history", help="Inspect bounded historical root-page evidence from web archives"
    )
    history_parser.add_argument("domain", help="Authorized domain")
    history_parser.add_argument("--max", type=int, default=4, dest="max_results", help="Max snapshots (1-4)")
    history_parser.add_argument("--json", action="store_true", dest="as_json")

    domains_parser = subparsers.add_parser(
        "domains", help="List a run's discovered domains as machine-readable output for downstream tooling"
    )
    domains_parser.add_argument("run_id", help="Run ID")
    domains_parser.add_argument("--json", action="store_true", dest="as_json", help="Output JSON")

    eval_parser = subparsers.add_parser(
        "eval", help="Evaluate a saved graph against a versioned ground-truth case (offline)"
    )
    eval_parser.add_argument("graph", help="Path to a .graph.json export")
    eval_parser.add_argument("--case", required=True, dest="case_path", help="Path to an evaluation case JSON")
    eval_parser.add_argument("--json", action="store_true", dest="as_json", help="Output machine-readable JSON")

    config_parser = subparsers.add_parser(
        "config", help="Show or edit persistent config (~/.reconrelate/config.json)"
    )
    config_sub = config_parser.add_subparsers(dest="config_command")
    config_sub.add_parser("show", help="Show effective settings, API keys, and source pins")
    set_p = config_sub.add_parser(
        "set", help="Set a value: model, fast_model, allow_cloud, api_base, key.<NAME>, source.<capability>"
    )
    set_p.add_argument("key", help="e.g. model, allow_cloud, key.WHOXY_API_KEY, source.reverse_whois")
    set_p.add_argument("value", help="Value to store")
    unset_p = config_sub.add_parser("unset", help="Remove a stored value")
    unset_p.add_argument("key", help="Same key form as `set`")
    config_sub.add_parser("path", help="Print the config file path")

    db_parser = subparsers.add_parser("db", help="Check, back up, restore, or retain the local database")
    db_sub = db_parser.add_subparsers(dest="db_command")
    db_check = db_sub.add_parser("check", help="Run SQLite integrity, foreign-key, and schema checks")
    db_check.add_argument("--json", action="store_true", dest="as_json")
    db_backup = db_sub.add_parser("backup", help="Create and verify a consistent online backup")
    db_backup.add_argument("--out", default=None, help="Backup path (default: timestamped beside database)")
    db_backup.add_argument("--force", action="store_true", help="Replace an existing --out file")
    db_backup.add_argument("--json", action="store_true", dest="as_json")
    db_restore = db_sub.add_parser("restore", help="Verify and restore a backup with a safety backup")
    db_restore.add_argument("backup", help="Backup database to restore")
    db_restore.add_argument("--yes", action="store_true", help="Confirm replacement of the active database")
    db_restore.add_argument("--json", action="store_true", dest="as_json")
    db_retention = db_sub.add_parser("retention", help="Preview or apply deletion of old runs/cache")
    db_retention.add_argument("--before", required=True, help="Delete runs created before ISO-8601 cutoff")
    db_retention.add_argument("--cache-before", default=None, help="Also delete cache older than this cutoff")
    db_retention.add_argument("--apply", action="store_true", help="Apply changes; default is preview only")
    db_retention.add_argument("--yes", action="store_true", help="Confirm --apply and its automatic backup")
    db_retention.add_argument("--json", action="store_true", dest="as_json")
    return parser


def _handle_run(args: argparse.Namespace, settings: Settings) -> int:
    import asyncio
    from reconrelate.llm_orchestration.relationship_engine import is_cloud_model
    if not getattr(args, "as_json", False):
        print_banner()
    if args.model is not None:
        settings.llm_model = args.model.strip()
    if args.fast_model is not None:
        settings.fast_model = args.fast_model.strip()
    if getattr(args, "expand_acquisitions", False):
        settings.expand_acquisitions = True
    if getattr(args, "historical_web", False):
        settings.historical_web = True
    if args.profile is not None:
        settings.provider_tier = args.profile
    if args.max_provider_calls is not None:
        if args.max_provider_calls < 0:
            print("Error: --max-provider-calls must be >= 0.", file=sys.stderr)
            return 2
        settings.max_provider_calls = args.max_provider_calls
    if args.max_billable_units is not None:
        if args.max_billable_units < 0:
            print("Error: --max-billable-units must be >= 0.", file=sys.stderr)
            return 2
        settings.max_billable_units = args.max_billable_units
    if settings.provider_tier == "byok":
        if not args.approve_paid:
            print("Error: --profile byok requires --approve-paid for this run.", file=sys.stderr)
            return 2
        if settings.max_billable_units <= 0:
            print("Error: BYOK runs require a positive --max-billable-units ceiling.", file=sys.stderr)
            return 2
        settings.paid_approved = True
    elif args.approve_paid:
        print("Error: --approve-paid is valid only with --profile byok.", file=sys.stderr)
        return 2
    for argument, field in (
        (args.max_model_calls, "max_model_calls"),
        (args.max_model_input_tokens, "max_model_input_tokens"),
        (args.max_model_output_tokens, "max_model_output_tokens"),
        (args.max_cloud_tokens, "max_cloud_tokens"),
        (args.max_cloud_cost_usd, "max_cloud_cost_usd"),
    ):
        if argument is not None:
            if argument < 0:
                print(f"Error: --{field.replace('_', '-')} must be >= 0.", file=sys.stderr)
                return 2
            setattr(settings, field, argument)
    # Quick mode is a fast spot-check. The LLM is an optional aid for ambiguous pivots — not
    # needed for the deterministic + Wikidata acquisition path — and a cold local model adds
    # minutes of latency. So quick mode skips it by default; deep mode and an explicit
    # --max-model-calls still enable it.
    if args.max_model_calls is None and getattr(args, "run_mode", None) == "quick":
        settings.max_model_calls = 0
    configured_models = [settings.llm_model or "qwen2.5:7b-instruct"]
    if settings.fast_model:
        configured_models.append(settings.fast_model)
    cloud_models = [model for model in configured_models if is_cloud_model(model)]
    if cloud_models:
        if not args.approve_cloud:
            print("Error: any cloud primary/fast model requires --approve-cloud for this run.", file=sys.stderr)
            return 2
        if not settings.llm_allow_cloud:
            print(
                "Error: cloud models are disabled; set config allow_cloud true before approval.",
                file=sys.stderr,
            )
            return 2
        if settings.max_cloud_tokens <= 0:
            print("Error: cloud models require a positive --max-cloud-tokens ceiling.", file=sys.stderr)
            return 2
        if settings.max_cloud_cost_usd <= 0:
            print("Error: cloud models require a positive --max-cloud-cost-usd ceiling.", file=sys.stderr)
            return 2
        settings.cloud_approved = True
    elif args.approve_cloud:
        print("Error: --approve-cloud is valid only for a configured cloud model.", file=sys.stderr)
        return 2
    runtime = build_runtime(settings, ollama_model=args.model, fast_model=args.fast_model)
    try:
        summary = asyncio.run(
            runtime.orchestrator.run(
                root_domain=args.domain,
                max_depth=args.max_depth,
                pivot_top_k=args.pivot_top_k,
                resume=args.resume,
                force_refresh=args.refresh,
            )
        )
        if args.as_json:
            print(json.dumps(asdict(summary), indent=2, sort_keys=True))
        else:
            print(f"Run ID: {summary.run_id}")
            print(f"Status: {summary.status}")
            print(f"Root: {summary.root_domain}")
            print(f"Domains: {summary.domains_count}")
            print(f"Identifiers: {summary.identifiers_count}")
            print(f"Edges: {summary.edges_count}")
        if not args.no_save and settings.auto_save_artifacts:
            out_dir = Path(args.artifacts_dir or settings.artifacts_dir)
            graph = runtime.repository.get_run_graph(summary.run_id)
            resolved = write_run_bundle(graph, summary.run_id, out_dir, summary.root_domain)
            if not args.as_json:
                print(f"Artifacts: {resolved}")
        return 0
    finally:
        runtime.close()


def _handle_tree(args: argparse.Namespace, settings: Settings) -> int:
    runtime = build_runtime(settings)
    try:
        graph = runtime.repository.get_run_graph(args.run_id)
        print(render_graph_json(graph) if args.format == "json" else render_ascii_tree(graph))
        return 0
    finally:
        runtime.close()


def _handle_report(args: argparse.Namespace, settings: Settings) -> int:
    runtime = build_runtime(settings)
    try:
        graph = runtime.repository.get_run_graph(args.run_id)
        if args.format == "json":
            print(render_graph_json(graph))
        else:
            print(render_markdown_report(graph))
        return 0
    finally:
        runtime.close()


def _handle_export(args: argparse.Namespace, settings: Settings) -> int:
    runtime = build_runtime(settings)
    try:
        graph = runtime.repository.get_run_graph(args.run_id)
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        # `export` stays split into separate machine-readable files: `eval` and `comparison`
        # consume the .graph.json directly, so that stays a stable, separately-addressable
        # artifact. Only the stem changes, from an opaque run id to <domain>-<n>, matching the
        # auto-saved bundle. The combined single-file view is what `run` writes.
        from reconrelate.output.artifacts import next_run_index, safe_domain_stem
        run_row = graph.get("run") or {}
        domain = str(run_row["root_domain"] if "root_domain" in run_row else "")
        stem = f"{safe_domain_stem(domain)}-{next_run_index(domain, out_dir)}"
        paths = (
            out_dir / f"{stem}.tree.txt",
            out_dir / f"{stem}.graph.json",
            out_dir / f"{stem}.report.md",
        )
        paths[0].write_text(render_ascii_tree(graph), encoding="utf-8")
        paths[1].write_text(render_graph_json(graph), encoding="utf-8")
        paths[2].write_text(render_markdown_report(graph), encoding="utf-8")
        if os.name != "nt":
            for p in paths:
                try:
                    os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
                except OSError:
                    pass
        print(f"Exported artifacts to: {out_dir.resolve()}")
        for p in paths:
            print(f"  {p.name}")
        return 0
    finally:
        runtime.close()


def _handle_providers(args: argparse.Namespace, settings: Settings) -> int:
    import asyncio

    from reconrelate.data_gathering.doctor import configuration_diagnostics, live_diagnostics
    from reconrelate.data_gathering.registry import default_registry

    balance_args = (args.provider, args.approve_paid, args.max_billable_units)
    if args.providers_action != "balance" and any(value is not None and value is not False for value in balance_args):
        raise ValueError("--provider/--approve-paid/--max-billable-units require `providers balance`")

    if args.providers_action == "balance":
        if any((args.live, args.target, args.run_id, args.baseline, args.candidate,
                args.case_path, args.manifest)):
            raise ValueError("providers balance accepts only --provider, --approve-paid, --max-billable-units, and --json")
        if args.provider != "whoxy":
            raise ValueError("providers balance requires --provider whoxy")
        if not args.approve_paid:
            raise SecurityError("providers balance requires --approve-paid")
        if (args.max_billable_units is None or not math.isfinite(args.max_billable_units)
                or args.max_billable_units < 1):
            raise SecurityError("providers balance requires --max-billable-units of at least 1")
        if not os.getenv("WHOXY_API_KEY", "").strip():
            raise SecurityError("WHOXY_API_KEY is required for providers balance")

        from reconrelate.core.provider_execution import ExecutionBudget, ProviderExecutor
        from reconrelate.core.provider_quota import ProviderQuotaSnapshot
        reg = default_registry()
        info = next(
            item for item in reg.infos("reverse_whois") if item.name == "whoxy"
        )
        provider = reg.get("reverse_whois", "whoxy")
        if provider is None:
            raise SecurityError("Whoxy is disabled or unavailable")
        telemetry = []
        budget = ExecutionBudget(max_calls=1, max_billable_units=args.max_billable_units)
        executor = ProviderExecutor(
            timeout_sec=info.effective_timeout(settings.request_timeout_sec),
            retry_count=0,
            execution_budget=budget,
            telemetry_sink=telemetry.append,
        )
        snapshot = asyncio.run(executor.execute(
            run_id=None,
            provider="whoxy",
            capability="reverse_whois",
            operation="balance",
            call=provider.balance,
            validator=lambda result: isinstance(result, ProviderQuotaSnapshot),
            billable=True,
            units=1,
            concurrency_limit=info.effective_concurrency_limit(),
            rate_limit_per_minute=info.effective_rate_limit(),
            max_response_bytes=info.max_response_bytes,
            max_result_items=info.max_result_items,
            max_requests_per_attempt=1,
            max_pages_per_attempt=1,
            timeout_sec=info.effective_timeout(settings.request_timeout_sec),
        ))
        payload = {
            **asdict(snapshot),
            "reserved_billable_units": budget.billable_units_reserved,
            "request_attempts": telemetry[-1].attempts if telemetry else 0,
        }
        if args.as_json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(
                f"Whoxy reverse-WHOIS balance: {snapshot.remaining} {snapshot.unit}s "
                f"(billing effect: {snapshot.billing_effect}; 1 unit conservatively reserved)"
            )
        return 0

    if args.providers_action == "benchmark":
        if not args.manifest:
            raise ValueError("providers benchmark requires --manifest FILE")
        if any((args.live, args.target, args.run_id, args.baseline, args.candidate, args.case_path)):
            raise ValueError("providers benchmark accepts only --manifest and --json")
        from reconrelate.quality.benchmark import render_benchmark, run_benchmark
        payload = run_benchmark(args.manifest)
        print(json.dumps(payload.to_dict(), indent=2, sort_keys=True) if args.as_json else render_benchmark(payload))
        return 0

    if args.providers_action == "compare":
        if not args.baseline or not args.candidate or not args.case_path:
            raise ValueError("providers compare requires --baseline, --candidate, and --case")
        if args.live or args.target or args.run_id or args.manifest:
            raise ValueError("providers compare is offline; --live, --target, and --run-id are not valid")
        from reconrelate.quality.comparison import compare_graphs, render_comparison
        from reconrelate.quality.evaluation import EvaluationCase
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        candidate = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
        if not isinstance(baseline, dict) or not isinstance(candidate, dict):
            raise ValueError("baseline and candidate graph exports must be JSON objects")
        payload = compare_graphs(
            baseline, candidate, EvaluationCase.from_path(args.case_path)
        )
        print(json.dumps(payload.to_dict(), indent=2, sort_keys=True) if args.as_json else render_comparison(payload))
        return 0

    if args.providers_action == "value":
        if not args.run_id:
            raise ValueError("providers value requires --run-id ID")
        if args.live or args.target or args.baseline or args.candidate or args.case_path or args.manifest:
            raise ValueError("providers value accepts only --run-id and --json")
        from reconrelate.core.provider_value import build_provider_value_report, render_provider_value_report
        from reconrelate.db.db import get_connection, init_db
        from reconrelate.db.repositories import GraphRepository
        conn = get_connection(settings.db_path)
        try:
            init_db(conn)
            graph = GraphRepository(conn).get_run_graph(args.run_id)
            payload = build_provider_value_report(graph)
        finally:
            conn.close()
        print(json.dumps(payload, indent=2, sort_keys=True) if args.as_json else render_provider_value_report(payload))
        return 0

    reg = default_registry()
    infos = reg.infos()
    if args.providers_action == "doctor":
        if args.run_id or args.baseline or args.candidate or args.case_path or args.manifest:
            raise ValueError("providers doctor does not accept value/compare arguments")
        if args.live:
            if not args.target:
                raise ValueError("providers doctor --live requires --target <authorized-domain>")
            payload = asyncio.run(live_diagnostics(
                reg, target=args.target, timeout_sec=settings.request_timeout_sec,
            ))
            if args.as_json:
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(
                    f"Live provider doctor for {payload['target']} "
                    f"({payload['network_calls']} upstream requests across "
                    f"{payload['provider_attempts']} provider attempts, 0 billable calls)"
                )
                for item in payload["providers"]:
                    print(f"- {item['capability']}/{item['name']}: {item['live_status']}")
            return 0
        diagnostics = configuration_diagnostics(reg)
        payload = {
            "network_calls": 0,
            "billable_calls": 0,
            "ready": sum(1 for item in diagnostics if item["available"]),
            "configuration_missing": sum(1 for item in diagnostics if not item["available"]),
            "providers": diagnostics,
        }
        if args.as_json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print("Provider doctor (configuration-only; 0 network calls, 0 billable calls)")
            for item in diagnostics:
                if item["disabled"]:
                    details = "disabled by RECONRELATE_DISABLE_PROVIDERS"
                elif item["available"]:
                    details = "ready"
                elif item.get("missing_executables"):
                    details = "missing executable " + ",".join(item["missing_executables"])
                else:
                    details = "missing " + ",".join(item["missing_environment"])
                policy = item["data_policy"]
                print(
                    f"- {item['capability']}/{item['name']}: {details} "
                    f"[{item['result_contract']}; cache={policy['cross_run_cache']}; "
                    f"export={policy['export_scope']}; raw={policy['raw_retention']}]"
                )
        return 0
    if (args.live or args.target or args.run_id or args.baseline or args.candidate
            or args.case_path or args.manifest):
        raise ValueError(
            "--live/--target require `providers doctor`; --run-id requires `providers value`; "
            "--baseline/--candidate/--case require `providers compare`"
        )
    if args.as_json:
        print(json.dumps([info.diagnostic() for info in infos], indent=2, sort_keys=True))
        return 0
    cap_w = max((len(i.capability) for i in infos), default=10)
    name_w = max((len(i.name) for i in infos), default=8)
    print(f"{'CAPABILITY':<{cap_w}}  {'SOURCE':<{name_w}}  {'TIER':<4}  {'STATUS':<12}  DESCRIPTION")
    for i in sorted(infos, key=lambda p: (p.capability, 0 if p.tier == 'paid' else 1, p.name)):
        diagnostic = i.diagnostic()
        if diagnostic["disabled"]:
            status = "disabled"
        elif i.available():
            status = "active"
        else:
            status = "needs " + ",".join(i.requires_env)
        policy = diagnostic["data_policy"]
        print(
            f"{i.capability:<{cap_w}}  {i.name:<{name_w}}  {i.tier:<4}  {status:<12}  "
            f"{i.description} [cache={policy['cross_run_cache']}; "
            f"export={policy['export_scope']}; raw={policy['raw_retention']}]"
        )
    return 0


def _handle_clusters(args: argparse.Namespace, settings: Settings) -> int:
    from reconrelate.output.clusters import compute_shared_clusters, render_clusters

    runtime = build_runtime(settings)
    try:
        graph = runtime.repository.get_run_graph(args.run_id)
        clusters = compute_shared_clusters(graph, min_domains=args.min_domains)
        print(render_clusters(clusters))
        return 0
    finally:
        runtime.close()


def _handle_domains(args: argparse.Namespace, settings: Settings) -> int:
    """Emit the run's discovered domains (+ depth + discovery method) as a stable interchange
    format for downstream tooling to rank, filter, or prioritize."""
    runtime = build_runtime(settings)
    try:
        graph = runtime.repository.get_run_graph(args.run_id)
        items: list[dict] = []
        for node in graph["nodes"]:
            if node["node_type"] != "domain":
                continue
            try:
                meta = json.loads(node.get("metadata_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                meta = {}
            discovered_by = meta.get("discovered_by") or (
                "reverse_whois" if "discovered_by_identifier" in meta else ""
            )
            items.append({
                "domain": node["value_norm"],
                "depth": meta.get("first_seen_depth"),
                "discovered_by": discovered_by,
            })
        if args.as_json:
            print(json.dumps(items, indent=2))
        else:
            for it in items:
                depth = "-" if it["depth"] is None else it["depth"]
                print(f"{it['domain']}\tdepth={depth}\t{it['discovered_by']}")
        return 0
    finally:
        runtime.close()


def _handle_config(args: argparse.Namespace, settings: Settings) -> int:
    from reconrelate.config import config_file as cf

    cmd = getattr(args, "config_command", None)
    if cmd == "path":
        print(cf.config_path())
        return 0
    if cmd == "set":
        try:
            env_name = cf.set_value(args.key, args.value)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        shown = cf.mask(args.value) if cf.is_secret(env_name) else args.value
        print(f"Set {env_name} = {shown}")
        print(f"Saved to {cf.config_path()}")
        return 0
    if cmd == "unset":
        try:
            env_name = cf.unset_value(args.key)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(f"Unset {env_name}")
        return 0
    # default (including bare `config`): show
    from reconrelate.config import model_profiles as mp
    print(cf.render_show(settings))
    print()
    print(mp.render_list(mp.load_store()))
    return 0


def _handle_model(args: argparse.Namespace, settings: Settings) -> int:
    from reconrelate.config import config_file as cf
    from reconrelate.config import model_profiles as mp

    cmd = getattr(args, "model_command", None)
    try:
        if cmd == "add":
            store = mp.load_store()
            provider = args.provider or mp.infer_provider(args.model_id)
            key_env = args.key_env
            if args.key_value is not None:
                key_env = (key_env or mp.default_key_env(provider)).strip().upper()
                if not key_env:
                    raise mp.ModelProfileError(
                        "--key-value requires --key (no default key env name for this provider)"
                    )
                cf.set_value(f"key.{key_env}", args.key_value)
            profile = mp.add_profile(
                store, name=args.name, provider=provider, model_id=args.model_id,
                api_base=args.api_base, key_env=key_env,
                input_price=args.input_price, output_price=args.output_price,
            )
            mp.save_store(store)
            print(f"Added profile {profile.name!r}: {profile.provider}/{profile.model_id}")
            print(f"Assign it with: reconrelate model use {profile.name} --role primary|fast")
            return 0
        if cmd == "use":
            store = mp.load_store()
            mp.use_profile(store, args.name, args.role)
            mp.save_store(store)
            profile = mp.active_profile(store, args.role)
            print(f"{args.name!r} is now the {args.role} model.")
            if profile is not None and profile.is_cloud():
                print(
                    "This is a cloud model: a run still needs --approve-cloud, allow_cloud=true, "
                    "and positive --max-cloud-tokens / --max-cloud-cost-usd ceilings. Assigning a "
                    "profile to a role does not grant permission to spend on it."
                )
            return 0
        if cmd == "remove":
            store = mp.load_store()
            mp.remove_profile(store, args.name)
            mp.save_store(store)
            print(f"Removed profile {args.name!r}.")
            return 0
        if cmd == "show":
            store = mp.load_store()
            profile = store.profiles.get(args.name)
            if profile is None:
                raise mp.ModelProfileError(f"no such profile: {args.name!r}")
            if getattr(args, "as_json", False):
                print(json.dumps(profile.to_dict(), indent=2, sort_keys=True))
            else:
                print(mp.render_list(mp.ProfileStore(profiles={args.name: profile}, roles=store.roles)))
            return 0
        # default (including bare `model` and `model list`): list
        store = mp.load_store()
        if getattr(args, "as_json", False):
            payload = {
                "profiles": {name: p.to_dict() for name, p in store.profiles.items()},
                "roles": store.roles,
            }
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(mp.render_list(store))
        return 0
    except mp.ModelProfileError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _handle_models(args: argparse.Namespace, settings: Settings) -> int:
    from reconrelate.llm_orchestration.model_catalog import (
        catalog_payload,
        diagnose_model,
        render_model_doctor,
    )

    if args.models_action == "benchmark":
        import asyncio

        from reconrelate.llm_orchestration.model_budget import ModelBudget
        from reconrelate.llm_orchestration.relationship_engine import LLMClient, is_cloud_model
        from reconrelate.quality.model_benchmark import (
            ModelBenchmarkManifest,
            render_model_benchmark,
            run_model_benchmark,
        )

        if not args.manifest:
            raise ValueError("models benchmark requires --manifest")
        model = (args.model or settings.llm_model or "qwen2.5:7b-instruct").strip()
        for argument, field in (
            (args.max_model_calls, "max_model_calls"),
            (args.max_model_input_tokens, "max_model_input_tokens"),
            (args.max_model_output_tokens, "max_model_output_tokens"),
            (args.max_cloud_tokens, "max_cloud_tokens"),
            (args.max_cloud_cost_usd, "max_cloud_cost_usd"),
        ):
            if argument is not None:
                if argument < 0:
                    raise ValueError(f"--{field.replace('_', '-')} must be >= 0")
                setattr(settings, field, argument)
        if is_cloud_model(model):
            if not settings.llm_allow_cloud:
                raise SecurityError("cloud models are disabled; set config allow_cloud true")
            if not args.approve_cloud:
                raise SecurityError("a cloud model benchmark requires --approve-cloud")
            if settings.max_cloud_tokens <= 0:
                raise SecurityError("a cloud model benchmark requires positive --max-cloud-tokens")
            if settings.max_cloud_cost_usd <= 0:
                raise SecurityError("a cloud model benchmark requires positive --max-cloud-cost-usd")
        elif args.approve_cloud:
            raise SecurityError("--approve-cloud is valid only for a cloud model")
        manifest = ModelBenchmarkManifest.from_path(args.manifest)
        telemetry = []
        client = LLMClient(
            model=model,
            api_base=settings.ollama_api_base,
            timeout_sec=settings.llm_timeout_sec,
            budget=ModelBudget(
                settings.max_model_calls,
                settings.max_model_input_tokens,
                settings.max_model_output_tokens,
                settings.max_cloud_tokens,
                math.ceil(settings.max_cloud_cost_usd * 1_000_000),
            ),
            telemetry_sink=telemetry.append,
        )
        result = asyncio.run(run_model_benchmark(manifest, client, telemetry))
        print(
            json.dumps(result.to_dict(), indent=2, sort_keys=True)
            if args.as_json else render_model_benchmark(result)
        )
        return 0
    if args.models_action == "doctor":
        result = diagnose_model(settings)
        print(
            json.dumps(result.to_dict(), indent=2, sort_keys=True)
            if args.as_json else render_model_doctor(result)
        )
        return 0 if result.ready else 1
    payload = catalog_payload()
    if args.as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print(f"Model catalog: {payload['catalog_version']}")
    print(f"Automatic recommendation: none ({payload['recommendation_reason']})")
    for model in payload["models"]:
        pricing = (
            f" price=${model['input_usd_per_million']}/${model['output_usd_per_million']} per M in/out"
            if model.get("input_usd_per_million") else ""
        )
        print(
            f"  {model['model']} [{model['runtime']}] compatibility={model['compatibility']} "
            f"quality={model['quality_status']}{pricing}"
        )
    return 0


def _handle_acquisitions(args: argparse.Namespace, settings: Settings) -> int:
    import asyncio
    from reconrelate.data_gathering.registry import default_registry

    registry = default_registry()
    providers = (
        registry.get_all("acquisitions") if args.source == "auto"
        else [registry.get("acquisitions", name=args.source)]
    )
    providers = [provider for provider in providers if provider is not None]
    if not providers:
        print("No acquisitions provider available.", file=sys.stderr)
        return 1
    async def collect() -> list[dict]:
        rows: list[dict] = []
        results = await asyncio.gather(*(
            provider.related_orgs(args.org, max_results=args.max_results)
            for provider in providers
        ), return_exceptions=True)
        for provider, result in zip(providers, results):
            source = str(getattr(provider, "__reconrelate_provider__", "unknown"))
            if isinstance(result, Exception):
                print(f"Warning: {source} failed: {result}", file=sys.stderr)
                continue
            for relation in result:
                rows.append({**relation, "source": source})
        return rows[:max(0, args.max_results)]

    relations = asyncio.run(collect())
    if not relations:
        print(f"No corporate relations found for {args.org!r}.")
        return 0
    if args.as_json:
        print(json.dumps(relations, indent=2, sort_keys=True))
        return 0
    print(f"Org relations for {args.org!r}:\n")
    for r in relations:
        identifier = r.get("lei") or r.get("qid") or "-"
        print(f"  [{r['source']}/{r['relation']}] {r['org']}  ({identifier})")
    return 0


def _handle_history(args: argparse.Namespace, settings: Settings) -> int:
    import asyncio
    from reconrelate.data_gathering.registry import default_registry

    if not 1 <= args.max_results <= 4:
        print("Error: --max must be between 1 and 4.", file=sys.stderr)
        return 2
    provider = default_registry().get("historical_web", name="wayback")
    if provider is None:
        print("No historical-web provider available.", file=sys.stderr)
        return 1
    records = asyncio.run(provider.lookup(args.domain, max_results=args.max_results))
    rows = [asdict(record) for record in records]
    if args.as_json:
        print(json.dumps(rows, indent=2, sort_keys=True))
        return 0
    if not rows:
        print(f"No archived root-page evidence found for {args.domain!r}.")
        return 0
    print(f"Historical root-page evidence for {args.domain!r}:\n")
    for row in rows:
        signals = []
        if row["copyright_org"]:
            signals.append(f"copyright={row['copyright_org']}")
        if row["tracker_ids"]:
            signals.append(f"trackers={','.join(row['tracker_ids'])}")
        suffix = f"  {'; '.join(signals)}" if signals else ""
        print(f"  [{row['captured_at']}] {row['title'] or '(untitled)'}{suffix}")
        print(f"    {row['archive_url']}")
    return 0


def _handle_plan(args: argparse.Namespace, settings: Settings) -> int:
    from reconrelate.core.normalize import normalize_domain
    from reconrelate.core.query_plan import build_query_plan, render_query_plan
    from reconrelate.data_gathering.registry import default_registry
    from reconrelate.security.safe_target import validate_scan_target

    domain = normalize_domain(args.domain)
    validate_scan_target(domain)
    if args.profile is not None:
        settings.provider_tier = args.profile
    if args.max_provider_calls is not None:
        if args.max_provider_calls < 0:
            print("Error: --max-provider-calls must be >= 0.", file=sys.stderr)
            return 2
        settings.max_provider_calls = args.max_provider_calls
    if args.max_billable_units is not None:
        if args.max_billable_units < 0:
            print("Error: --max-billable-units must be >= 0.", file=sys.stderr)
            return 2
        settings.max_billable_units = args.max_billable_units
    if args.expand_acquisitions:
        settings.expand_acquisitions = True
    if args.historical_web:
        settings.historical_web = True
    plan = build_query_plan(settings, default_registry(), paid_approved=args.approve_paid)
    if args.as_json:
        payload = plan.to_dict()
        payload["domain"] = domain
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_query_plan(plan))
    return 0


def _handle_eval(args: argparse.Namespace, settings: Settings) -> int:
    """Evaluate existing artifacts without network, provider, database, or model calls."""
    from reconrelate.quality.evaluation import EvaluationCase, evaluate_graph, render_evaluation

    graph_path = Path(args.graph)
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    if not isinstance(graph, dict):
        raise ValueError("graph export must be a JSON object")
    case = EvaluationCase.from_path(args.case_path)
    result = evaluate_graph(graph, case)
    if args.as_json:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        print(render_evaluation(result))
    return 0


def _handle_db(args: argparse.Namespace, settings: Settings) -> int:
    from reconrelate.db.operations import (
        apply_retention,
        backup_database,
        check_database,
        restore_database,
    )

    command = args.db_command
    if command == "check":
        result = check_database(settings.db_path)
        if args.as_json:
            print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        else:
            print(f"Database: {result.path}")
            print(f"Status: {'ok' if result.ok else 'FAILED'}")
            print(f"Integrity: {result.integrity}")
            print(f"Foreign-key violations: {result.foreign_key_violations}")
            print(f"Migrations: {', '.join(map(str, result.migration_versions)) or 'none'}")
            print(f"Required schema: {'present' if result.required_tables_present else 'missing'}")
        return 0 if result.ok else 1
    if command == "backup":
        destination = backup_database(settings.db_path, args.out, overwrite=args.force)
        result = check_database(destination)
        payload = {"backup": str(destination), "check": result.to_dict()}
        print(json.dumps(payload, indent=2, sort_keys=True) if args.as_json else f"Backup verified: {destination}")
        return 0
    if command == "restore":
        if not args.yes:
            raise ValueError("restore requires --yes because it replaces the active database")
        result = restore_database(args.backup, settings.db_path)
        payload = asdict(result)
        if args.as_json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"Database restored: {result.restored_path}")
            print(f"Source backup: {result.source_backup}")
            print(f"Pre-restore safety backup: {result.safety_backup or 'not needed'}")
        return 0
    if command == "retention":
        preview = apply_retention(
            settings.db_path,
            run_before=args.before,
            cache_before=args.cache_before,
            apply=False,
        )
        safety_backup = None
        if args.apply:
            if not args.yes:
                raise ValueError("retention --apply requires --yes")
            if preview.runs or preview.cache_entries:
                safety_backup = str(backup_database(settings.db_path))
            result = apply_retention(
                settings.db_path,
                run_before=args.before,
                cache_before=args.cache_before,
                apply=True,
            )
        else:
            result = preview
        payload = {**result.to_dict(), "safety_backup": safety_backup}
        if args.as_json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"Mode: {'applied' if result.apply else 'preview'}")
            print(f"Runs matched: {result.runs}")
            print(f"Cache entries matched: {result.cache_entries}")
            if safety_backup:
                print(f"Safety backup: {safety_backup}")
            elif not result.apply:
                print("No data changed. Add --apply --yes to execute with an automatic backup.")
        return 0
    raise ValueError("choose a database command: check, backup, restore, or retention")


def main(argv: list[str] | None = None) -> int:
    # Load persisted config into the environment before building Settings (env still wins).
    from reconrelate.config.config_file import apply_config_to_env
    from reconrelate.config.model_profiles import apply_profiles_to_env
    apply_config_to_env()
    # Expand any active model-profile role assignment (reconrelate model use ...) into the
    # same LLM_MODEL / FAST_LLM_MODEL / OLLAMA_API_BASE env vars Settings.from_env() reads.
    apply_profiles_to_env()
    parser = _build_parser()
    args = parser.parse_args(_normalize_cli_argv(list(sys.argv[1:] if argv is None else argv)))
    configure_logging(verbose=getattr(args, "verbose", False))
    run_mode_cli = getattr(args, "run_mode", None) if args.command in {"run", "plan"} else None
    budget_cli = getattr(args, "budget", None) if args.command in {"run", "plan"} else None
    settings = Settings.from_env(run_mode_cli=run_mode_cli, budget_cli=budget_cli)
    if not args.command:
        # A real terminal with nothing piped in -> the interactive shell. A script or a pipe
        # (sys.stdin.isatty() is False) keeps the old scriptable behavior unchanged.
        if sys.stdin.isatty():
            from reconrelate.cli.shell import run_shell
            return run_shell()
        parser.print_help()
        return 1

    try:
        if args.command == "run":
            return _handle_run(args, settings)
        if args.command == "plan":
            return _handle_plan(args, settings)
        if args.command == "tree":
            return _handle_tree(args, settings)
        if args.command == "report":
            return _handle_report(args, settings)
        if args.command == "export":
            return _handle_export(args, settings)
        if args.command == "providers":
            return _handle_providers(args, settings)
        if args.command == "models":
            return _handle_models(args, settings)
        if args.command == "model":
            return _handle_model(args, settings)
        if args.command == "clusters":
            return _handle_clusters(args, settings)
        if args.command == "acquisitions":
            return _handle_acquisitions(args, settings)
        if args.command == "history":
            return _handle_history(args, settings)
        if args.command == "config":
            return _handle_config(args, settings)
        if args.command == "domains":
            return _handle_domains(args, settings)
        if args.command == "eval":
            return _handle_eval(args, settings)
        if args.command == "db":
            return _handle_db(args, settings)
        parser.print_help()
        return 1
    except SecurityError as exc:
        print(f"Security policy: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
