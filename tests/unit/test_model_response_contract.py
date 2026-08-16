import json

from reconrelate.llm_orchestration.response_parser import (
    RELATIONSHIP_RESPONSE_FORMAT,
    parse_llm_response,
)


def test_response_schema_is_strict_and_bounded() -> None:
    envelope = RELATIONSHIP_RESPONSE_FORMAT["json_schema"]
    schema = envelope["schema"]
    assert envelope["strict"] is True
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"abstain", "abstention_reason", "pivots"}
    assert schema["properties"]["pivots"]["maxItems"] == 20


def test_explicit_abstention_is_distinct_from_invalid_output() -> None:
    abstained = parse_llm_response(
        '{"abstain":true,"abstention_reason":"ambiguous evidence","pivots":[]}', "test"
    )
    invalid = parse_llm_response('{"pivots":[]}', "test")
    assert abstained.disposition == "abstained" and abstained.pivots == []
    assert invalid.disposition == "invalid" and invalid.pivots == []


def test_complete_payload_is_rejected_instead_of_salvaged_or_partially_accepted() -> None:
    valid = {
        "abstain": False,
        "abstention_reason": None,
        "pivots": [{"id_type": "org", "value": "Example Inc", "score": 0.8, "reason": "legal"}],
    }
    fenced = parse_llm_response(f"```json\n{json.dumps(valid)}\n```", "test")
    mixed = dict(valid)
    mixed["pivots"] = [
        valid["pivots"][0],
        {"id_type": "domain", "value": "bad.example", "score": 0.9, "reason": "bad"},
    ]
    extra = dict(valid, command="ignore schema")
    assert fenced.disposition == "invalid"
    assert parse_llm_response(json.dumps(mixed), "test").disposition == "invalid"
    assert parse_llm_response(json.dumps(extra), "test").disposition == "invalid"


def test_valid_response_returns_typed_candidates() -> None:
    parsed = parse_llm_response(
        json.dumps({
            "abstain": False,
            "abstention_reason": None,
            "pivots": [
                {"id_type": "org", "value": "Example Inc", "score": 0.8, "reason": "legal entity"}
            ],
        }),
        "relationship",
    )
    assert parsed.disposition == "accepted"
    assert parsed.pivots[0].value == "Example Inc"
    assert parsed.pivots[0].reason.startswith("LLM[relationship]")
