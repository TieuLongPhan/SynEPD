import pytest
from synepd.construct.checks import (
    validate_for_construction,
    ensure_valid_for_construction,
    ConstructionValidationError,
)
from synepd.models import Case


def test_validate_for_construction():
    # Empty cases validation
    report = validate_for_construction([])
    assert not report.passed
    assert "No cases were provided" in report.message()

    case_data1 = {
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

    case1 = Case.from_dict(case_data1)
    case2 = Case.from_dict(case_data1)  # Duplicate

    # Test duplicates check
    report = validate_for_construction([case1, case2])
    assert not report.passed
    assert "Duplicate case_id values" in report.message()

    # Test mismatch with summary check
    summary = {"case_count": 5}
    report = validate_for_construction([case1], summary=summary)
    assert not report.passed
    assert "summary case_count" in report.message()

    # Valid check
    report = validate_for_construction(
        [case1], summary={"case_count": 1, "level4_count": 1}
    )
    assert report.passed

    # ensure_valid_for_construction raises exception on failure
    with pytest.raises(ConstructionValidationError):
        ensure_valid_for_construction([])
