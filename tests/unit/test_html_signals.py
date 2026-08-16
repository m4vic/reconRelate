from reconrelate.core.normalize import normalize_identifier
import asyncio

from reconrelate.core.provider_result import ProviderResult, observations_from_result
from reconrelate.core.types import BasicIntelRecord, WhoisRecord
from reconrelate.llm_orchestration.deterministic_scorer import extract_deterministic_pivots
from reconrelate.core.tracker import is_plausible_tracker, tracker_confidence
from reconrelate.data_gathering.basic_info_provider import (
    BasicInfoProvider,
    _extract_legal_entities,
    _extract_relationship_signals,
    _legal_page_urls,
)

SAMPLE = """
<html><head>
<script>gtag('config','G-ABC1234XYZ');</script>
<!-- GTM-AB12CD -->
ga('create','UA-123456-1','auto');
<meta name="google-adsense-account" content="ca-pub-1234567890123456">
</head><body>
<footer>&copy; 2026 Acme Holdings, Inc. All rights reserved.</footer>
</body></html>
"""


def test_extracts_all_tracker_ids() -> None:
    trackers, _ = _extract_relationship_signals(SAMPLE)
    assert "UA-123456-1" in trackers
    assert "G-ABC1234XYZ" in trackers
    assert "GTM-AB12CD" in trackers
    assert "ca-pub-1234567890123456" in trackers


def test_extracts_copyright_entity() -> None:
    _, org = _extract_relationship_signals(SAMPLE)
    assert "Acme Holdings" in org
    assert "all rights reserved" not in org.lower()
    assert _extract_relationship_signals(
        "© 2020–2026 Example Legal LLC. All rights reserved"
    )[1] == "Example Legal LLC"


def test_no_signals_on_plain_html() -> None:
    trackers, org = _extract_relationship_signals("<html><body>hi</body></html>")
    assert trackers == [] and org == ""


def test_tracker_normalizes_to_upper() -> None:
    assert normalize_identifier("tracker", "ua-123456-1") == "UA-123456-1"


def test_placeholder_trackers_are_rejected_and_family_specificity_is_scored() -> None:
    html = "G-XXXXXXXXXX GTM-XXXXXXX G-ABCDEF12 ca-pub-1234567890123456"
    trackers, _ = _extract_relationship_signals(html)
    assert trackers == ["G-ABCDEF12", "ca-pub-1234567890123456"]
    assert is_plausible_tracker("G-XXXXXXXXXX") is False
    assert tracker_confidence("ca-pub-1234567890123456") > tracker_confidence("G-ABCDEF12")


def test_legal_page_links_are_same_site_allowlisted_and_bounded() -> None:
    html = """
      <a href="/privacy-policy">Privacy</a>
      <a href="https://www.example.com/legal#top">Legal</a>
      <a href="https://social.example.net/privacy">Social</a>
      <a href="/products">Products</a>
      <a href="/terms">Terms</a>
    """
    assert _legal_page_urls(html, "https://example.com/home", "example.com") == [
        "https://example.com/privacy-policy", "https://www.example.com/legal"
    ]


def test_legal_entity_extraction_requires_label_and_corporate_suffix() -> None:
    html = """
      <h1>Privacy</h1><p>This website is operated by Example Holdings, Inc.</p>
      <p>Our brand is Wonderful Example.</p><p>Support: Example Team</p>
    """
    assert _extract_legal_entities(html) == ["Example Holdings, Inc"]
    assert _extract_legal_entities("<p>Operated by an independent team.</p>") == []


class _FakeBasic(BasicInfoProvider):
    async def _fetch_html(self, domain: str) -> tuple[str, str]:
        return '<title>Old Brand</title><a href="/privacy">Privacy</a>', "https://newbrand.example/"

    async def _fetch_url(self, url: str) -> tuple[str, str]:
        return "<p>Legal entity: New Brand Holdings LLC.</p>", url


def test_lookup_preserves_redirect_and_legal_page_provenance() -> None:
    record = asyncio.run(_FakeBasic().lookup("oldbrand.test"))
    assert record.redirect_domain == "newbrand.example"
    assert record.final_url == "https://newbrand.example/"
    # The legal URL resolves against the redirected site, but cross-site pages are intentionally
    # not fetched for an old-domain scan.
    assert record.legal_entities == []


class _FakeSameSiteBasic(BasicInfoProvider):
    async def _fetch_html(self, domain: str) -> tuple[str, str]:
        return '<a href="/privacy">Privacy</a>', "https://www.example.com/"

    async def _fetch_url(self, url: str) -> tuple[str, str]:
        return "<p>Legal entity: Example Holdings LLC.</p>", url


def test_same_site_legal_entity_becomes_sourced_observation() -> None:
    record = asyncio.run(_FakeSameSiteBasic().lookup("example.com"))
    assert record.legal_entities == ["Example Holdings LLC"]
    assert record.legal_entity_sources["Example Holdings LLC"] == "https://www.example.com/privacy"
    result = ProviderResult.from_data("http-html", "basic_info", record, subject="example.com")
    observation = next(item for item in observations_from_result(result) if item.predicate == "states_legal_entity")
    assert observation.object_value_norm == "Example Holdings LLC"
    assert observation.source_record_id == "https://www.example.com/privacy"


def test_labelled_legal_entity_is_a_high_quality_deterministic_pivot() -> None:
    pivots = extract_deterministic_pivots(
        WhoisRecord(domain="example.com"),
        BasicIntelRecord(domain="example.com", legal_entities=["Example Holdings LLC"]),
        "example.com",
    )
    legal = next(item for item in pivots if item.reason == "html: labelled legal-page entity")
    assert legal.id_type == "org"
    assert legal.score == 0.85
