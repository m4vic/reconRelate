"""
ReconRelate - Domain Reconnaissance Mapper (quick launcher)

The zero-config "paste a domain and go" entry point. Accepts a bare domain OR a full URL,
and runs a FAST SCOUT (budget=low, shallow) so a first look returns quickly instead of
crawling a large org for minutes. For deeper maps, acquisitions expansion, JSON output, or
resuming a run, use the full CLI:  python -m reconrelate.cli run <domain> --budget medium|max

Usage:   python run.py <domain-or-url>
Example: python run.py roche.com
         python run.py https://www.roche.com/about   (URL is fine - it's normalized)
"""
import sys
import logging
from pathlib import Path

# Add src/ so imports work without pip install
sys.path.insert(0, str(Path(__file__).parent / "src"))

from reconrelate.config.settings import Settings
from reconrelate.core.errors import SecurityError, ValidationError
from reconrelate.core.factory import build_runtime
from reconrelate.core.normalize import normalize_domain, registrable_domain
from reconrelate.output.artifacts import write_run_bundle
from reconrelate.output.renderers import render_ascii_tree

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")


def main():
    if len(sys.argv) < 2:
        if sys.stdin.isatty():
            from reconrelate.cli.shell import run_shell
            sys.exit(run_shell())
        print("Usage: python run.py <domain-or-url>")
        print("Example: python run.py roche.com")
        sys.exit(1)

    # Accept a pasted URL or a bare domain; collapse to the apex (registrable) domain,
    # so https://www.roche.com/about -> roche.com (ReconRelate maps owned apexes, not subdomains).
    try:
        domain = registrable_domain(normalize_domain(sys.argv[1]))
    except ValidationError as e:
        print(f"Not a valid domain/URL: {e}")
        print("Try:  python run.py roche.com")
        sys.exit(1)

    # Load persisted config/model-profile selection the same way the full CLI does, so a
    # `reconrelate model use ...` selection isn't silently ignored by this quick launcher.
    from reconrelate.config.config_file import apply_config_to_env
    from reconrelate.config.model_profiles import apply_profiles_to_env
    apply_config_to_env()
    apply_profiles_to_env()

    # Fast scout by default: low budget = shallow, quick first look. Deeper crawls go
    # through the full CLI (see the module docstring / the hint printed at the end).
    settings = Settings.from_env(budget_cli="low")
    model = (settings.llm_model or "qwen3.5:9b").strip()

    print("=" * 52)
    print("  ReconRelate - Domain Recon (fast scout)")
    print(f"  Target:  {domain}")
    print(f"  Model:   {model}   (needs Ollama running for LLM steps)")
    print(f"  Budget:  low / scout  ->  quick, shallow first look")
    print("=" * 52)

    runtime = build_runtime(settings)

    try:
        summary = runtime.orchestrator.run(
            root_domain=domain,
            max_depth=None,
            pivot_top_k=settings.pivot_top_k,
        )

        print("\n" + "=" * 52)
        print("  RESULTS")
        print("=" * 52)
        print(f"  Status:        {summary.status}")
        print(f"  Root Domain:   {summary.root_domain}")
        print(f"  Domains Found: {summary.domains_count}")
        print(f"  Identifiers:   {summary.identifiers_count}")
        print(f"  Connections:   {summary.edges_count}")
        print(f"  Run ID:        {summary.run_id}")
        print("=" * 52)

        graph = runtime.repository.get_run_graph(summary.run_id)
        if settings.auto_save_artifacts:
            resolved = write_run_bundle(graph, summary.run_id, Path(settings.artifacts_dir))
            print(f"  Artifacts:     {resolved}")
        if graph:
            print("\n" + "=" * 52)
            print("  GRAPH TREE")
            print("=" * 52)
            print(render_ascii_tree(graph))
            print("=" * 52)

        # Point the way deeper without cluttering the simple path.
        print("\n  This was a fast scout. For a deeper map:")
        print(f"    python -m reconrelate.cli run {domain} --budget medium --acquisitions")
        print(f"    python -m reconrelate.cli clusters {summary.run_id}   # shared-operator clusters")

    except SecurityError as e:
        print(f"\nSecurity policy: {e}")
        sys.exit(2)
    except Exception as e:
        print(f"\nError: {e}")

    finally:
        runtime.close()


if __name__ == "__main__":
    main()
