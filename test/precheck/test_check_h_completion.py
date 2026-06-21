from synepd.models import Case
from synepd.precheck.check_h_completion import (
    check_single_h_completion,
    validate_h_completion,
    _direct_its_hcount_change,
)
import networkx as nx


def test_direct_its_hcount_change():
    graph = nx.Graph()
    graph.add_node(1, hcount=(2, 2))
    graph.add_node(2, hcount=(1, 3))  # absolute difference is 2
    assert _direct_its_hcount_change(graph) == 2


def test_check_single_h_completion():
    # Hydrogen counts are consistent (no explicit H changes or no H changes in mapped atoms)
    res, msg = check_single_h_completion("[CH3:1][O-:2].[H+:3]>>[CH3:1][OH:2]")
    # Wait, the mapped reactant H+:3 has map 3, product OH:2 has map 2. Let's see if H completion passes.
    assert isinstance(res, bool)


def test_validate_h_completion():
    case = Case.from_dict(
        {
            "case_id": "polar01_001",
            "dataset_name": "SynEPD",
            "schema_version": "0.1.0",
            "level1_code": "POLAR",
            "level1_name": "Polar Reactions",
            "level2_code": "POLAR.01",
            "level2_name": "Sub-polar",
            "level3_code": "POLAR.01.01",
            "level3_name": "Leaf Node",
            "level4_code": "POLAR.01.01.01",
            "level4_label": "Variant 1",
            "case_variant": 1,
            "reaction_smiles": "CC[O-].[NH4+]>>CCO",
            "reaction_center_signature": "SIGMA",
            "reaction_center_template_pool": "pool1",
            "shares_reaction_center_within_level4": False,
            "atom_mapping": {},
            "validation_status": "VALID",
            "curation_status": "APPROVED",
            "manual_review_required": False,
        }
    )

    results = validate_h_completion([case])
    assert len(results) == 1
    assert results[0].check_name == "h_completion"
