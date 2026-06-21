"""Tests for synepd.models."""

from synepd.models import AtomMappingInfo, Case

_RAW = {
    "case_id": "SYNMES-0000001",
    "dataset_name": "SynEPD",
    "schema_version": "0.1.0",
    "level1_code": "POLAR",
    "level1_name": "Polar / ionic / two-electron chemistry",
    "level2_code": "POLAR.01",
    "level2_name": "Proton / ion / Lewis acid–base transfer",
    "level3_code": "POLAR.01.01",
    "level3_name": "Heteroatom proton transfer",
    "level4_code": "POLAR.01.01.001",
    "level4_label": "Alcohol protonation / deprotonation",
    "case_variant": 1,
    "reaction_smiles": "[CH3:1][O:2][H:3].[NH3:4]>>[CH3:1][O-:2].[NH3+:4][H:3]",
    "reaction_center_signature": "O-H_to_N",
    "reaction_center_template_pool": "proton_ion",
    "reaction_center_uniqueness_scope": "within_level4",
    "shares_reaction_center_within_level4": False,
    "atom_mapping": {
        "mapped_reaction_center": True,
        "map_consistency_checked": True,
        "reactant_product_atom_map_sets_match": True,
        "explicit_hydrogen_in_reaction_center": True,
        "unmapped_explicit_hydrogen_present": False,
        "mapped_atom_count": 4,
    },
    "validation_status": "parse_pass_map_consistent_unique_rc",
    "curation_status": "template_seed_case",
    "manual_review_required": False,
}


def test_case_from_dict_roundtrip():
    case = Case.from_dict(_RAW)
    assert case.case_id == "SYNMES-0000001"
    assert case.level1_code == "POLAR"
    assert case.level4_label == "Alcohol protonation / deprotonation"
    assert case.case_variant == 1
    assert case.atom_mapping.mapped_atom_count == 4


def test_case_from_dict_old_name_field():
    """v0.1.0 schema uses level4_label; older records may use name."""
    raw = {**_RAW}
    del raw["level4_label"]
    raw["name"] = "Old label field"
    case = Case.from_dict(raw)
    assert case.level4_label == "Old label field"


def test_atom_mapping_defaults():
    ami = AtomMappingInfo.from_dict({})
    assert ami.mapped_atom_count == 0
    assert ami.mapped_reaction_center is False


def test_case_notes_optional():
    case = Case.from_dict(_RAW)
    assert case.notes == ""
