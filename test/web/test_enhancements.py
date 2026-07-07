import pytest
from synepd.web.server import (
    get_reaction_detail,
    get_reactions_by_arrow_count,
    get_reactions_by_arrow_type,
    get_reactions_by_signature,
    search_reactions,
    list_reaction_centers,
)


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
