import pytest
import networkx as nx
from synepd.web.server import (
    app,
    get_reaction_detail,
    get_reactions_by_arrow_count,
    get_reactions_by_arrow_type,
    get_reactions_by_signature,
    search_reactions,
    list_reaction_centers,
    export_reactions_bulk,
    ExportBulkRequest,
    serialize_graph,
)
from synepd.web.knowledge_graph import kg_reactions_by_context_hash


def test_new_reaction_fields():
    search_res = search_reactions(query="POLAR")
    if search_res["results"]:
        rxn_id = search_res["results"][0]["id"]
        detail = get_reaction_detail(rxn_id)
        assert "balanced" in detail
        assert "reactant_atom_count" in detail
        assert "product_atom_count" in detail
        assert "formal_charge_delta" in detail
        assert "rdkit_coords" in detail
        assert "epd_representation" in detail
        assert "canonical_aam_key" in detail
        assert detail["mechanism_context"]


def test_mechanistic_roles_survive_web_serialization():
    graph = nx.Graph()
    graph.add_node(1, element="C", atom_map=1, mechanistic_roles=["epd_context"])
    graph.add_node(2, element="O", atom_map=2)
    graph.add_edge(
        1,
        2,
        order=(1.0, 1.0),
        mechanistic_roles=["transition"],
    )

    payload = serialize_graph(graph)

    assert payload["nodes"][0]["mechanistic_roles"] == ["epd_context"]
    assert payload["links"][0]["mechanistic_roles"] == ["transition"]


def test_v1_api_aliases_cover_core_and_knowledge_graph_routes():
    paths = set(app.openapi()["paths"])
    assert "/api/v1/health" in paths
    assert "/api/v1/reactions/{reaction_id}" in paths
    assert "/api/v1/kg/search" in paths
    assert "/api/v1/kg/reactions-by-context-hash" in paths

    ordered_paths = [
        route.path
        for route in app.routes
        if hasattr(route, "path") and route.path.startswith("/api/v1/reactions/")
    ]
    assert ordered_paths.index("/api/v1/reactions/export-bulk") < ordered_paths.index(
        "/api/v1/reactions/{reaction_id}"
    )


def test_knowledge_graph_can_group_exact_mechanistic_contexts():
    detail = get_reaction_detail(1)
    context_hash = detail["mechanism_context"]["context_hash"]

    result = kg_reactions_by_context_hash(context_hash)

    assert result["total"] >= 1
    assert any(item["ref_id"] == 1 for item in result["results"])


def test_by_arrow_count():
    data = get_reactions_by_arrow_count(n=3, limit=5)
    assert "total" in data
    assert "results" in data
    if data["results"]:
        assert "case_id" in data["results"][0]


def test_by_arrow_type():
    data = get_reactions_by_arrow_type(code="LP-/Sigma+", mode="contains", limit=5)
    assert "total" in data
    assert "results" in data
    if data["results"]:
        assert "case_id" in data["results"][0]


def test_by_signature():
    data = get_reactions_by_signature(
        pattern="LP-/Sigma+", match_type="subsequence", limit=5
    )
    assert "total" in data
    assert "results" in data
    if data["results"]:
        assert "case_id" in data["results"][0]


def test_reaction_centers_smarts():
    data = list_reaction_centers(limit=5)
    assert "results" in data
    if data["results"]:
        assert "smarts" in data["results"][0]


def test_export_reactions_bulk():
    search_res = search_reactions(query="POLAR")
    if search_res["results"]:
        rxn_id = search_res["results"][0]["id"]
        req = ExportBulkRequest(reaction_ids=[rxn_id], template_ids=[])
        response = export_reactions_bulk(req)
        assert response.status_code == 200
        import json

        data = json.loads(response.body.decode("utf-8"))
        assert len(data) == 1
        assert data[0]["id"] == rxn_id
        assert "atom_mapped_smiles" in data[0]


def test_export_reactions_bulk_by_template():
    centers = list_reaction_centers(limit=1)
    assert centers["results"]

    template_id = centers["results"][0]["id"]
    req = ExportBulkRequest(reaction_ids=[], template_ids=[template_id])
    response = export_reactions_bulk(req)

    assert response.status_code == 200
    import json

    data = json.loads(response.body.decode("utf-8"))
    assert data
    assert all("atom_mapped_smiles" in item for item in data)
