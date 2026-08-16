import asyncio
from reconrelate.core.types import BasicIntelRecord, WhoisRecord
from reconrelate.llm_orchestration.relationship_engine import RelationshipEngine


class RecordingLLM:
    """Stand-in LLM client that counts calls (no network / Ollama needed)."""

    def __init__(self) -> None:
        self.calls = 0

    async def call_unified(self, domain, evidence, run_metadata=None):  # noqa: ANN001
        self.calls += 1
        return []


def _engine(llm: RecordingLLM, escalate_only: bool = True) -> RelationshipEngine:
    return RelationshipEngine(llm_client=llm, escalate_only=escalate_only)


def test_skips_llm_when_strong_whois_pivot() -> None:
    llm = RecordingLLM()
    whois = WhoisRecord(domain="corp.com", registrant_email="it@corp.com")
    pivots = asyncio.run(_engine(llm).select_pivots("corp.com", whois, BasicIntelRecord(domain="corp.com"), top_k=5))
    assert llm.calls == 0
    assert any(p.id_type == "email" for p in pivots)


def test_tracker_id_is_strong_and_skips_llm() -> None:
    llm = RecordingLLM()
    intel = BasicIntelRecord(domain="x.com", tracker_ids=["UA-12345-6"])
    pivots = asyncio.run(_engine(llm).select_pivots("x.com", WhoisRecord(domain="x.com"), intel, top_k=5))
    assert llm.calls == 0
    assert any(p.id_type == "tracker" and p.value == "UA-12345-6" for p in pivots)


def test_escalates_to_llm_when_baseline_weak() -> None:
    llm = RecordingLLM()
    intel = BasicIntelRecord(domain="weak.com", aliases=["Weak"])  # alias score 0.45 < strong
    asyncio.run(_engine(llm).select_pivots("weak.com", WhoisRecord(domain="weak.com"), intel, top_k=5))
    assert llm.calls == 1


def test_escalate_only_false_always_calls() -> None:
    llm = RecordingLLM()
    whois = WhoisRecord(domain="corp.com", registrant_email="it@corp.com")
    asyncio.run(_engine(llm, escalate_only=False).select_pivots("corp.com", whois, BasicIntelRecord(domain="corp.com"), top_k=5))
    assert llm.calls == 1
