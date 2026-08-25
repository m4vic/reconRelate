from __future__ import annotations

import logging
import os
import re
import stat
from pathlib import Path

from reconrelate.output.renderers import (
    render_ascii_tree,
    render_graph_json,
    render_markdown_report,
)

logger = logging.getLogger(__name__)

# Windows-reserved device names; a file called e.g. "con.md" is not creatable there.
_RESERVED_STEMS = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def safe_domain_stem(domain: str) -> str:
    """Filesystem-safe stem for a domain, preserving readability.

    Dots are kept (automattic.com stays automattic.com) since they are legal in filenames on
    every supported platform and dropping them hurts the readability this naming exists for.
    Anything outside [A-Za-z0-9._-] is replaced, so an IDN or a malformed value can never
    produce a path separator, a drive prefix, or a parent-directory traversal.
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", (domain or "").strip().lower()).strip("._-")
    if not cleaned:
        cleaned = "unknown"
    if cleaned.split(".", 1)[0] in _RESERVED_STEMS:
        cleaned = f"_{cleaned}"
    return cleaned[:80]


def next_run_index(domain: str, out_dir: Path) -> int:
    """1-based sequence number for this domain's next bundle in `out_dir`.

    Derived from existing files rather than tracked state, so the numbering stays correct if a
    user deletes or archives old bundles, and never silently overwrites one. Matches any
    suffix after `<domain>-<n>` so the single-file bundle written by `run` and the split
    machine-readable files written by `export` share one sequence in a shared directory.
    """
    stem = safe_domain_stem(domain)
    pattern = re.compile(rf"^{re.escape(stem)}-(\d+)(?:\.|$)", re.IGNORECASE)
    highest = 0
    if out_dir.exists():
        for existing in out_dir.iterdir():
            match = pattern.match(existing.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return highest + 1


def render_run_bundle(graph: dict, run_id: str) -> str:
    """One self-contained markdown document: report, tree, and full graph JSON.

    Previously a run wrote three separate run-id-named files, which meant finding a past scan
    required knowing its UUID. Everything a run produced now lives in a single file named for
    the domain, with the run id recorded inside for cross-referencing the database.
    """
    return "\n".join((
        render_markdown_report(graph),
        "",
        "## Relationship tree",
        "",
        "```text",
        render_ascii_tree(graph).rstrip("\n"),
        "```",
        "",
        "## Graph data (JSON)",
        "",
        "Domains, identifiers, edges, claims, and their supporting evidence.",
        "",
        "```json",
        render_graph_json(graph).rstrip("\n"),
        "```",
        "",
        f"<!-- run_id: {run_id} -->",
        "",
    ))


def write_run_bundle(graph: dict, run_id: str, out_dir: Path, root_domain: str = "") -> Path:
    """Persist one combined markdown bundle named `<domain>-<n>.md`.

    Returns the path of the written file (not just its directory), so the caller can show the
    user exactly where the scan landed.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    run = graph.get("run") or {}
    domain = root_domain or str(run["root_domain"] if "root_domain" in run else "")
    stem = safe_domain_stem(domain)
    path = out_dir / f"{stem}-{next_run_index(domain, out_dir)}.md"
    path.write_text(render_run_bundle(graph, run_id), encoding="utf-8")
    if os.name != "nt":
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
    logger.info("Saved run bundle to %s", path.resolve())
    return path.resolve()
