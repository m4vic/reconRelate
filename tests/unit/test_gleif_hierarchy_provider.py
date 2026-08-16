import asyncio

from reconrelate.data_gathering.gleif_hierarchy_provider import GleifHierarchyProvider


def _record(lei: str, name: str, *, status: str = "ISSUED") -> dict:
    return {
        "type": "lei-records", "id": lei,
        "attributes": {
            "lei": lei,
            "entity": {"legalName": {"name": name}, "otherNames": [],
                       "transliteratedOtherNames": [], "status": "ACTIVE"},
            "registration": {"status": status},
        },
    }


class FakeGleif(GleifHierarchyProvider):
    def __init__(self, records: list[dict], relations: dict[str, object] | None = None) -> None:
        self.records = records
        self.relations = relations or {}
        self.paths: list[str] = []

    async def _get_json(self, path: str, params: dict | None = None) -> dict:
        self.paths.append(path)
        if path == "/lei-records":
            return {"data": self.records}
        return {"data": self.relations.get(path)}


def test_exact_unique_name_returns_typed_accounting_relations() -> None:
    provider = FakeGleif(
        [_record("CHILD", "Example Holdings, Inc.")],
        {
            "/lei-records/CHILD/direct-parent": _record("PARENT", "Example Parent plc"),
            "/lei-records/CHILD/direct-children": [_record("SUB", "Example Subsidiary LLC")],
        },
    )
    rows = asyncio.run(provider.related_orgs("example holdings inc", max_results=10))
    assert {(row["relation"], row["lei"]) for row in rows} == {
        ("direct_accounting_parent", "PARENT"),
        ("direct_accounting_child", "SUB"),
    }
    assert rows[0]["domain"] == ""
    assert rows[0]["source_record_id"].startswith("CHILD:")


def test_ambiguous_exact_name_abstains_without_relationship_calls() -> None:
    provider = FakeGleif([_record("ONE", "Same Corp"), _record("TWO", "Same Corp")])
    assert asyncio.run(provider.related_orgs("Same Corp")) == []
    assert provider.paths == ["/lei-records"]


def test_fuzzy_or_lapsed_match_abstains() -> None:
    fuzzy = FakeGleif([_record("ONE", "Example Corporation")])
    lapsed = FakeGleif([_record("ONE", "Example Corp", status="LAPSED")])
    assert asyncio.run(fuzzy.related_orgs("Example Corp")) == []
    assert asyncio.run(lapsed.related_orgs("Example Corp")) == []


def test_alternate_name_can_resolve_exactly() -> None:
    record = _record("ONE", "Formal Holdings Limited")
    record["attributes"]["entity"]["otherNames"] = [{"name": "Example Brand"}]
    provider = FakeGleif([record])
    assert asyncio.run(provider.related_orgs("Example Brand")) == []
    assert len(provider.paths) == 5  # exact resolution followed by four bounded relation reads


def test_zero_limit_avoids_network() -> None:
    provider = FakeGleif([_record("ONE", "Example")])
    assert asyncio.run(provider.related_orgs("Example", max_results=0)) == []
    assert provider.paths == []
