from reconrelate.core.types import WhoisRecord
from reconrelate.llm_orchestration.relationship_engine import _extract_whois_pivot_candidates


def test_dates_in_raw_do_not_become_phone_pivots() -> None:
    # WHOIS date/timestamp fields used to be matched as "phones" by the old regex.
    whois = WhoisRecord(
        domain="example.com",
        raw={"text": "Creation Date: 1995081404\nExpiry Date: 2026081304\nUpdated: 2026011618"},
    )
    cands = _extract_whois_pivot_candidates(whois, "example.com")
    assert not any(c.id_type == "phone" for c in cands)


def test_registrant_phone_field_becomes_one_pivot() -> None:
    whois = WhoisRecord(domain="corp.com", registrant_phone="+1.2025550100")
    phones = [c for c in _extract_whois_pivot_candidates(whois, "corp.com") if c.id_type == "phone"]
    assert len(phones) == 1
    assert phones[0].value == "+12025550100"


def test_no_phone_when_field_empty() -> None:
    whois = WhoisRecord(domain="corp.com", registrant_email="it@corp.com")
    assert not any(c.id_type == "phone" for c in _extract_whois_pivot_candidates(whois, "corp.com"))
