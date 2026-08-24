"""Render a real terminal SVG of the interactive shell for the README.

Not a mockup: it imports the actual BANNER/help text/prompt styling from
reconrelate.cli.shell and reconrelate.cli.app so the image can never drift from what the
shell really prints. Run manually when the shell's visual output changes:

    python scripts/render_screenshot.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from rich.console import Console

from reconrelate.cli import shell


def main() -> None:
    console = Console(record=True, width=100, file=open("nul" if sys.platform == "win32" else "/dev/null", "w"))

    console.print(shell.BANNER, style="bold red", highlight=False)
    console.print("  free-first domain & acquisition relationship mapping\n", style="red", highlight=False)
    console.print(
        "Type a domain to scan, [bold]/help[/bold] for commands, [bold]/exit[/bold] to leave.\n",
        highlight=False,
    )

    console.print("recon>", style="bold red", end=" ")
    console.print("automattic.com", highlight=False)
    console.print(
        "Quick scout: automattic.com  - deterministic only, no model calls (quick mode "
        "always skips the model, even if one is configured with /model use). "
        "For a model-assisted map: /run automattic.com --mode deep --acquisitions",
        style="dim", highlight=False,
    )
    console.print(
        "[dim]2026-08-24 19:09:59[/dim] [green]INFO[/green] Relationship mapping for "
        "automattic.com: 4 total candidates -> 4 after filter (top 8)",
        highlight=False,
    )
    console.print("Run ID: 40a882bd-0b70-4873-81d7-cca58e36ceaf", highlight=False)
    console.print("Status: [green]completed[/green]   Domains: 8   Identifiers: 14   Edges: 52", highlight=False)

    console.print()
    console.print("recon>", style="bold red", end=" ")
    console.print("/providers", highlight=False)
    console.print(
        "[bold]CAPABILITY[/bold]      [bold]SOURCE[/bold]      [bold]TIER[/bold]  [bold]STATUS[/bold]",
        highlight=False,
    )
    console.print("acquisitions    wikidata    free  [green]active[/green]", highlight=False)
    console.print("reverse_whois   duckduckgo  free  [green]active[/green]", highlight=False)
    console.print("subdomains      crtsh       free  [green]active[/green]", highlight=False)
    console.print("whois           rdap-iana   free  [green]active[/green]", highlight=False)

    out_dir = Path(__file__).parent.parent / "docs" / "img"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "reconrelate-shell.svg"
    console.save_svg(str(out_path), title="reconrelate")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
