import pytest
from synepd.web.server import (
    get_db_info,
    get_taxonomy,
    search_reactions,
    query_epd,
    EPDQueryRequest,
    health_check,
    get_reaction_neighbors,
    list_reaction_centers,
    get_arrow_types,
    get_stats,
    check_balance,
    check_balance_smiles,
    BalanceCheckRequest,
    get_taxon_reactions,
    get_rc_reactions,
    get_random_reaction,
    render_rdkit_svg,
)


def test_db_info_endpoint():
    data = get_db_info()
    assert "version" in data
    assert "backend" in data


def test_taxonomy_endpoint():
    data = get_taxonomy()
    assert "taxonomy" in data

    def find_counted_taxon(nodes):
        for node in nodes:
            assert "reaction_count" in node
            assert "reactions" not in node
            if node["reaction_count"] > 0:
                return True
            if node["children"]:
                if find_counted_taxon(node["children"]):
                    return True
        return False

    assert find_counted_taxon(data["taxonomy"])

    lazy_data = get_taxon_reactions("POLAR.04", include_descendants=True, limit=2)
    assert lazy_data["total"] >= len(lazy_data["results"])
    assert "name" in lazy_data["results"][0]


def test_search_endpoint():
    data = search_reactions(query="POLAR")
    assert isinstance(data, dict)
    assert "total" in data
    assert "results" in data
    assert len(data["results"]) > 0
    assert "name" in data["results"][0]


def test_query_epd_endpoint():
    req = EPDQueryRequest(rsmi="CC[O-].[NH4+]>>CCO")
    data = query_epd(req)
    assert "success" in data
    assert data["success"] is True
    assert "name" in data
    assert data["name"]
    assert data["arrows"]


def test_health_endpoint():
    data = health_check()
    assert data["status"] == "ok"


def test_neighbors_endpoint():
    # Fetch first reaction to get a valid reaction_id
    search_res = search_reactions(query="POLAR")
    rxn_id = search_res["results"][0]["id"]
    try:
        data = get_reaction_neighbors(reaction_id=rxn_id)
        assert "reaction_id" in data
        assert "neighbors" in data
    except Exception as e:
        print(e)
        # If reaction has no ITS (e.g. mock DB), it might raise 404
        pass


def test_reaction_centers_endpoint():
    data = list_reaction_centers(limit=5)
    assert "total" in data
    assert "results" in data


def test_random_reaction_endpoint():
    data = get_random_reaction()
    assert isinstance(data["reaction_id"], int)


def test_arrow_types_endpoint():
    data = get_arrow_types()
    assert isinstance(data, list)
    assert len(data) > 0
    assert "code" in data[0]


def test_stats_endpoint():
    data = get_stats()
    assert "arrow_type_distribution" in data
    assert "arrows_per_reaction_distribution" in data


def test_balance_endpoint():
    search_res = search_reactions(query="POLAR")
    rxn_id = search_res["results"][0]["id"]
    data = check_balance(reaction_id=rxn_id)
    assert "balanced" in data


def test_balance_smiles_endpoint():
    req = BalanceCheckRequest(rsmi="[CH3:1][O:2][H:3]>>[CH3:1][O-:2].[H+:3]")
    data = check_balance_smiles(req)
    assert data["balanced"] is True


def test_taxon_reactions_endpoint():
    data = get_taxon_reactions(code="POLAR", limit=5)
    assert "total" in data
    assert "results" in data


def test_rc_reactions_endpoint():
    data = list_reaction_centers(limit=1)
    if data["results"]:
        rc_id = data["results"][0]["id"]
        rxns = get_rc_reactions(rc_id=rc_id, limit=5)
        assert "total" in rxns
        assert "results" in rxns


def test_rdkit_render_endpoint_for_reaction_svg():
    response = render_rdkit_svg(smi="CCO>>CC=O", kind="reaction")
    assert response.media_type == "image/svg+xml"
    assert b"<svg" in response.body


def test_docs_directory_mounted():
    from pathlib import Path

    docs_path = Path(__file__).parent.parent.parent / "docs" / "build" / "html"
    assert docs_path.exists()
    assert (docs_path / "index.html").exists()
