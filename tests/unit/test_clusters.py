import json

from reconrelate.output.clusters import compute_shared_clusters, render_clusters


def _node(node_id, node_type, value, id_type=None):
    meta = json.dumps({"identifier_type": id_type}) if id_type else "{}"
    return {"id": node_id, "node_type": node_type, "value_norm": value, "metadata_json": meta}


def _edge(frm, to):
    return {"from_node_id": frm, "to_node_id": to}


GRAPH = {
    "nodes": [
        _node("a", "domain", "a.com"),
        _node("b", "domain", "b.com"),
        _node("c", "domain", "c.com"),
        _node("t", "identifier", "UA-1", "tracker"),
        _node("o", "identifier", "acme", "org"),
        _node("e", "identifier", "x@x.com", "email"),
    ],
    "edges": [
        _edge("a", "t"), _edge("b", "t"),   # a.com + b.com share a tracker
        _edge("a", "o"), _edge("c", "o"),   # a.com + c.com share an org
        _edge("a", "e"),                     # only a.com -> not a cluster
    ],
}


def test_clusters_group_domains_by_shared_identifier() -> None:
    clusters = compute_shared_clusters(GRAPH)
    assert len(clusters) == 2  # tracker + org; the single-domain email is excluded
    # tracker ranks first (strongest same-operator signal)
    assert clusters[0]["id_type"] == "tracker"
    assert clusters[0]["domains"] == ["a.com", "b.com"]
    assert clusters[1]["id_type"] == "org"


def test_direction_independent() -> None:
    g = {"nodes": GRAPH["nodes"], "edges": [_edge("t", "a"), _edge("b", "t")]}  # mixed directions
    clusters = compute_shared_clusters(g)
    assert clusters[0]["domains"] == ["a.com", "b.com"]


def test_min_domains_filter_and_empty_render() -> None:
    assert compute_shared_clusters(GRAPH, min_domains=3) == []
    assert "No shared-operator clusters" in render_clusters([])
