import asyncio
from reconrelate.data_gathering.wikidata_acquisitions_provider import WikidataAcquisitionsProvider


class FakeWikidata(WikidataAcquisitionsProvider):
    """Wikidata provider with canned API responses (no network)."""

    async def _get_json(self, params: dict) -> dict:
        if params["action"] == "wbsearchentities":
            return {"search": [{"id": "Q95"}]} if params["search"] == "Google" else {"search": []}
        # wbgetentities
        qid = params["ids"]
        if qid == "Q95":
            return {"entities": {"Q95": {
                "labels": {"en": {"value": "Google"}},
                "claims": {
                    "P355": [{"mainsnak": {"datavalue": {"value": {"id": "Q12345"}}}}],   # subsidiary
                    "P749": [{"mainsnak": {"datavalue": {"value": {"id": "Q20800404"}}}}],  # parent
                },
            }}}
        labels = {"Q12345": "Fitbit", "Q20800404": "Alphabet Inc."}
        return {"entities": {qid: {"labels": {"en": {"value": labels.get(qid, qid)}}, "claims": {}}}}


def test_related_orgs_returns_ownership_edges() -> None:
    rels = asyncio.run(FakeWikidata().related_orgs("Google"))
    orgs = {r["org"] for r in rels}
    assert "Fitbit" in orgs and "Alphabet Inc." in orgs
    by_org = {r["org"]: r["relation"] for r in rels}
    assert by_org["Fitbit"] == "subsidiary"
    assert by_org["Alphabet Inc."] == "parent"


def test_unknown_org_returns_empty() -> None:
    assert asyncio.run(FakeWikidata().related_orgs("NoSuchCompanyXYZ")) == []
