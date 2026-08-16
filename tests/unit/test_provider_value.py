from reconrelate.core.provider_value import build_provider_value_report, render_provider_value_report
from reconrelate.cli.app import main
from reconrelate.db.db import get_connection, init_db
from reconrelate.db.repositories import GraphRepository


def _evidence(source: str) -> dict:
    return {"source": source, "polarity": "supports"}


def test_value_report_counts_families_without_false_corroboration() -> None:
    graph = {
        "run": {"id": "run-1", "root_domain": "example.com"},
        "claims": [
            {
                "confidence_class": "verified", "object_type": "domain",
                "object_value_norm": "asset.example", "evidence": [
                    _evidence("rdap-iana"), _evidence("python-whois"),
                ],
            },
            {
                "confidence_class": "probable", "object_type": "domain",
                "object_value_norm": "acquired.example", "evidence": [
                    _evidence("http-html"), _evidence("sec-edgar"),
                ],
            },
            {
                "confidence_class": "verified", "object_type": "domain",
                "object_value_norm": "unknown.example", "evidence": [_evidence("mystery")],
            },
        ],
        "provider_usage": [{
            "provider": "rdap-iana", "capability": "whois", "status": "success",
            "calls": 1, "attempts": 1, "upstream_requests": 2, "pages": 1,
            "latency_ms": 10, "units": 0,
        }],
    }

    result = build_provider_value_report(graph)
    rows = {row["source_family"]: row for row in result["family_contributions"]}
    registration = rows["domain-registration-registry"]
    assert registration["supporting_claims"] == 1
    assert registration["sole_family_verified_claims"] == 1
    assert registration["sources"] == ["python-whois", "rdap-iana"]
    assert registration["sole_family_supported_domains"] == ["asset.example"]
    assert rows["current-origin-web"]["corroborated_claims"] == 1
    assert rows["sec-regulatory-filings"]["corroborated_claims"] == 1
    assert rows["unclassified"]["sole_family_claims"] == 0
    assert result["interpretation"]["sole_family_support_is_causal_lift"] is False
    assert result["network_calls_performed"] == 0

    rendered = render_provider_value_report(result)
    assert "attribution, not causal lift" in rendered
    assert "domain-registration-registry" in rendered


def test_contradicting_evidence_does_not_receive_support_credit() -> None:
    graph = {
        "run": {"id": "run-2", "root_domain": "example.com"},
        "claims": [{
            "confidence_class": "rejected", "object_type": "domain",
            "object_value_norm": "noise.example",
            "evidence": [{"source": "http-html", "polarity": "contradicts"}],
        }],
        "provider_usage": [],
    }

    assert build_provider_value_report(graph)["family_contributions"] == []


def test_cli_value_report_is_independent_of_provider_and_model_runtime(
    tmp_path, monkeypatch, capsys
) -> None:
    database = tmp_path / "value.sqlite"
    conn = get_connection(str(database))
    init_db(conn)
    run_id = GraphRepository(conn).create_run("example.com", 0, 1)
    conn.close()
    monkeypatch.setenv("RECONRELATE_DB_PATH", str(database))
    monkeypatch.setenv("LLM_MODEL", "gpt-5")
    monkeypatch.setenv("RECONRELATE_LLM_ALLOW_CLOUD", "false")

    assert main(["providers", "value", "--run-id", run_id, "--json"]) == 0
    output = capsys.readouterr().out
    assert '"offline": true' in output
    assert '"network_calls_performed": 0' in output
