"""Regression tests: API error responses must not echo backend exception text.

500-status details are already redacted centrally by the HTTPException handler
in ``synepd.web.server``; these tests pin the 4xx paths, which reach clients
verbatim.
"""

import pytest
from fastapi import HTTPException

from synepd.web.server import BalanceCheckRequest, check_balance_smiles
from synepd.web.knowledge_graph import kg_similar_reactions, kg_substructure_search


def test_balance_smiles_invalid_input_returns_authored_400():
    with pytest.raises(HTTPException) as exc_info:
        check_balance_smiles(BalanceCheckRequest(rsmi="not-a-reaction"))
    assert exc_info.value.status_code == 400
    # Authored ValueError message from check_reaction_balance, not a traceback.
    assert "reaction SMILES" in exc_info.value.detail
    assert "Traceback" not in exc_info.value.detail


def test_similar_reactions_redacts_fingerprint_failure():
    with pytest.raises(HTTPException) as exc_info:
        kg_similar_reactions(rsmi="][", top_k=5)
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "Could not fingerprint reaction SMILES"


def test_substructure_search_invalid_smarts_uses_fixed_message():
    with pytest.raises(HTTPException) as exc_info:
        kg_substructure_search(smarts="][")
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "Invalid SMARTS pattern"
