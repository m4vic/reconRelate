import json

from reconrelate.output.artifacts import (
    next_run_index,
    render_run_bundle,
    safe_domain_stem,
    write_run_bundle,
)


def _graph(root: str = "automattic.com") -> dict:
    return {
        "run": {
            "id": "run-1", "root_domain": root, "status": "completed", "max_depth": 1,
            "pivot_top_k": 5, "created_at": "2026-08-25T00:00:00+00:00",
            "provider_profile": "free", "max_provider_calls": 500, "max_billable_units": 0.0,
            "run_mode": "deep", "llm_model": "ollama/qwen2.5:7b-instruct",
            "llm_policy_version": "relationship-pivot-v2", "cache_mode": "reuse",
        },
        "nodes": [{"id": "n1", "node_type": "domain", "value_norm": root, "metadata_json": "{}"}],
        "edges": [],
        "lineage": [],
        "pivot_decisions": [],
        "observations": [],
        "claims": [],
        "claim_projection": {},
        "provider_usage": [],
        "model_usage": [],
        "model_budget_usage": [],
        "task_summary": {"pending": 0, "in_progress": 0, "succeeded": 1, "failed": 0},
    }


# ---- filename safety -------------------------------------------------------------------

def test_domain_stem_keeps_dots_for_readability() -> None:
    assert safe_domain_stem("automattic.com") == "automattic.com"
    assert safe_domain_stem("Linktr.EE") == "linktr.ee"


def test_domain_stem_strips_path_separators_and_traversal() -> None:
    for hostile in ("../../etc/passwd", "a/b", "a\\b", "C:evil"):
        stem = safe_domain_stem(hostile)
        assert "/" not in stem and "\\" not in stem and ":" not in stem
        assert not stem.startswith(".")


def test_domain_stem_handles_empty_and_reserved_windows_names() -> None:
    assert safe_domain_stem("") == "unknown"
    assert safe_domain_stem("   ") == "unknown"
    assert safe_domain_stem("con.com").startswith("_")
    assert safe_domain_stem("nul") == "_nul"


def test_domain_stem_is_length_bounded() -> None:
    assert len(safe_domain_stem("a" * 500 + ".com")) <= 80


# ---- sequence numbering ----------------------------------------------------------------

def test_index_starts_at_one_and_increments(tmp_path) -> None:
    assert next_run_index("automattic.com", tmp_path) == 1
    write_run_bundle(_graph(), "run-1", tmp_path)
    assert next_run_index("automattic.com", tmp_path) == 2
    write_run_bundle(_graph(), "run-2", tmp_path)
    assert next_run_index("automattic.com", tmp_path) == 3


def test_index_is_per_domain(tmp_path) -> None:
    write_run_bundle(_graph("automattic.com"), "run-1", tmp_path)
    write_run_bundle(_graph("automattic.com"), "run-2", tmp_path)
    assert next_run_index("mozilla.org", tmp_path) == 1


def test_index_shared_across_bundle_and_export_style_suffixes(tmp_path) -> None:
    # `run` writes <domain>-1.md; `export` writes <domain>-N.graph.json into the same dir.
    # Both must advance one sequence so numbering stays meaningful.
    write_run_bundle(_graph(), "run-1", tmp_path)
    (tmp_path / "automattic.com-2.graph.json").write_text("{}", encoding="utf-8")
    assert next_run_index("automattic.com", tmp_path) == 3


def test_index_survives_a_deleted_middle_bundle(tmp_path) -> None:
    write_run_bundle(_graph(), "run-1", tmp_path)
    write_run_bundle(_graph(), "run-2", tmp_path)
    write_run_bundle(_graph(), "run-3", tmp_path)
    (tmp_path / "automattic.com-2.md").unlink()
    # Highest-wins, so an existing bundle is never silently overwritten.
    assert next_run_index("automattic.com", tmp_path) == 4


def test_index_ignores_unrelated_and_similarly_prefixed_files(tmp_path) -> None:
    (tmp_path / "notes.md").write_text("x", encoding="utf-8")
    (tmp_path / "automattic.community-7.md").write_text("x", encoding="utf-8")
    assert next_run_index("automattic.com", tmp_path) == 1


def test_index_on_missing_directory(tmp_path) -> None:
    assert next_run_index("automattic.com", tmp_path / "nope") == 1


# ---- bundle contents -------------------------------------------------------------------

def test_bundle_is_one_file_named_for_the_domain(tmp_path) -> None:
    path = write_run_bundle(_graph(), "run-abc", tmp_path)
    assert path.name == "automattic.com-1.md"
    assert [p.name for p in tmp_path.iterdir()] == ["automattic.com-1.md"]


def test_second_run_writes_a_separate_file_and_does_not_overwrite(tmp_path) -> None:
    first = write_run_bundle(_graph(), "run-1", tmp_path)
    second = write_run_bundle(_graph(), "run-2", tmp_path)
    assert first.name == "automattic.com-1.md"
    assert second.name == "automattic.com-2.md"
    assert "run-1" in first.read_text(encoding="utf-8")
    assert "run-2" in second.read_text(encoding="utf-8")


def test_bundle_contains_report_tree_and_graph_json() -> None:
    text = render_run_bundle(_graph(), "run-abc")
    assert "## Relationship tree" in text
    assert "## Graph data (JSON)" in text
    assert "run_id: run-abc" in text  # recorded for cross-referencing the database


def test_bundle_graph_json_block_is_valid_json() -> None:
    text = render_run_bundle(_graph(), "run-abc")
    block = text.split("```json", 1)[1].split("```", 1)[0]
    assert json.loads(block)["run"]["root_domain"] == "automattic.com"


def test_bundle_falls_back_to_graph_root_domain_when_not_passed(tmp_path) -> None:
    path = write_run_bundle(_graph("mozilla.org"), "run-1", tmp_path)
    assert path.name == "mozilla.org-1.md"


def test_explicit_root_domain_argument_wins(tmp_path) -> None:
    path = write_run_bundle(_graph("mozilla.org"), "run-1", tmp_path, "override.example")
    assert path.name == "override.example-1.md"


def test_write_creates_missing_output_directory(tmp_path) -> None:
    target = tmp_path / "deep" / "nested"
    path = write_run_bundle(_graph(), "run-1", target)
    assert path.exists()
