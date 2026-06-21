from synepd.models import Case
from synepd.database.core import SynEPDDatabase, infer_hierarchy


def _case(case_id: str) -> Case:
    return Case.from_dict(
        {
            "case_id": case_id,
            "dataset_name": "SynEPD",
            "schema_version": "0.1.0",
            "level1_code": "POLAR",
            "level1_name": "Polar Reactions",
            "level2_code": "POLAR.01",
            "level2_name": "Proton Transfer",
            "level3_code": "POLAR.01.01",
            "level3_name": "Heteroatom proton transfer",
            "level4_code": "POLAR.01.01.001",
            "level4_label": "Alcohol protonation",
            "case_variant": 1,
            "reaction_smiles": "CC[O-].[NH4+]>>CCO.N",
            "reaction_center_signature": "SIGMA",
            "reaction_center_template_pool": "pool1",
            "shares_reaction_center_within_level4": False,
            "atom_mapping": {},
            "validation_status": "VALID",
            "curation_status": "APPROVED",
            "manual_review_required": False,
        }
    )


def test_synepd_database_in_memory():
    case = _case("C1")
    db = SynEPDDatabase((case,), summary={"case_count": 1})

    assert len(db) == 1
    assert db.get("C1") == case
    assert db.get("MISSING") is None
    assert db.require("C1") == case
    assert db.cases_for_code("POLAR") == (case,)
    assert db.cases_for_code("POLAR.01") == (case,)
    assert db.label_for_code("POLAR.01.01") == "Heteroatom proton transfer"
    assert db.case_count_by_level1() == {"POLAR": 1}
    assert db.level4_variant_counts() == {"POLAR.01.01.001": 1}

    hierarchy = infer_hierarchy((case,))
    assert "POLAR.01.01.001" in hierarchy
    node = hierarchy["POLAR.01.01.001"]
    assert node.name == "Alcohol protonation"
    assert node.level == 4
    assert node.parent_code == "POLAR.01.01"
