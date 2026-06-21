"""Data models for SynEPD v0.1.0 case records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class AtomMappingInfo:
    mapped_reaction_center: bool = False
    map_consistency_checked: bool = False
    reactant_product_atom_map_sets_match: bool = False
    explicit_hydrogen_in_reaction_center: bool = False
    unmapped_explicit_hydrogen_present: bool = False
    mapped_atom_count: int = 0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AtomMappingInfo":
        return cls(
            mapped_reaction_center=data.get("mapped_reaction_center", False),
            map_consistency_checked=data.get("map_consistency_checked", False),
            reactant_product_atom_map_sets_match=data.get(
                "reactant_product_atom_map_sets_match", False
            ),
            explicit_hydrogen_in_reaction_center=data.get(
                "explicit_hydrogen_in_reaction_center", False
            ),
            unmapped_explicit_hydrogen_present=data.get(
                "unmapped_explicit_hydrogen_present", False
            ),
            mapped_atom_count=data.get("mapped_atom_count", 0),
        )


@dataclass
class Case:
    case_id: str
    dataset_name: str
    schema_version: str
    level1_code: str
    level1_name: str
    level2_code: str
    level2_name: str
    level3_code: str
    level3_name: str
    level4_code: str
    level4_label: str
    case_variant: int
    reaction_smiles: str
    reaction_center_signature: str
    reaction_center_template_pool: str
    reaction_center_uniqueness_scope: str
    shares_reaction_center_within_level4: bool
    atom_mapping: AtomMappingInfo
    validation_status: str
    curation_status: str
    manual_review_required: bool
    notes: str = ""
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Case":
        return cls(
            case_id=data["case_id"],
            dataset_name=data.get("dataset_name", "SynEPD"),
            schema_version=data.get("schema_version", "0.1.0"),
            level1_code=data["level1_code"],
            level1_name=data["level1_name"],
            level2_code=data["level2_code"],
            level2_name=data["level2_name"],
            level3_code=data["level3_code"],
            level3_name=data["level3_name"],
            level4_code=data["level4_code"],
            # v0.1.0 uses level4_label; older records used name
            level4_label=data.get("level4_label", data.get("name", "")),
            case_variant=data["case_variant"],
            reaction_smiles=data["reaction_smiles"],
            reaction_center_signature=data["reaction_center_signature"],
            reaction_center_template_pool=data["reaction_center_template_pool"],
            reaction_center_uniqueness_scope=data.get(
                "reaction_center_uniqueness_scope", "within_level4"
            ),
            shares_reaction_center_within_level4=data.get(
                "shares_reaction_center_within_level4", False
            ),
            atom_mapping=AtomMappingInfo.from_dict(data.get("atom_mapping", {})),
            validation_status=data.get("validation_status", ""),
            curation_status=data.get("curation_status", ""),
            manual_review_required=data.get("manual_review_required", False),
            notes=data.get("notes", ""),
            raw=data,
        )
