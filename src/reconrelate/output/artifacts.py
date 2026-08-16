from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

from reconrelate.output.renderers import (
    render_ascii_tree,
    render_graph_json,
    render_markdown_report,
)

logger = logging.getLogger(__name__)


def write_run_bundle(graph: dict, run_id: str, out_dir: Path) -> Path:
    """
    Persist tree, graph JSON, and markdown report for a run in one shot.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / run_id
    paths = (
        base.with_suffix(".tree.txt"),
        base.with_suffix(".graph.json"),
        base.with_suffix(".report.md"),
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
    logger.info("Saved run bundle to %s", out_dir.resolve())
    return out_dir.resolve()
