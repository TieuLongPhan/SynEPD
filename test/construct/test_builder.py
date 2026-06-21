from pathlib import Path
from synepd.construct.builder import default_data_paths, _filtered_summary
from synepd.models import Case


def test_default_data_paths():
    paths = default_data_paths("/tmp/repo")
    assert paths["cases_full"] == Path("/tmp/repo/data/synepd_v0_1_0_cases_full.json")
    assert paths["sqlite"] == Path("/tmp/repo/data/synepd_v0_1_0.sqlite")


def test_filtered_summary():
    case_data = {
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
    case = Case.from_dict(case_data)
    summary = {"case_count": 2, "original_case_count": 2}
    adjusted = _filtered_summary(summary, (case,), original_count=2)
    assert adjusted["case_count"] == 1
    assert adjusted["original_case_count"] == 2
    assert adjusted["by_level1"] == {"POLAR": 1}
    assert adjusted["level4_count"] == 1
