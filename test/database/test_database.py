"""Tests for construct/database/query modules."""

from pathlib import Path

import pytest

from synepd.construct import (
    ConstructionValidationError,
    build_database,
    build_database_from_cases,
    build_sqlite_database_from_cases,
    validate_for_construction,
)
from synepd.database import SQLiteSynEPDDatabase
from synepd.models import Case
from synepd.query import Query, by_level, by_template_pool, find_cases, search_labels


def _raw_case(
    case_id: str,
    *,
    level1_code: str = "POLAR",
    level1_name: str = "Polar / ionic / two-electron chemistry",
    level2_code: str = "POLAR.01",
    level2_name: str = "Proton / ion / Lewis acid-base transfer",
    level3_code: str = "POLAR.01.01",
    level3_name: str = "Heteroatom proton transfer",
    level4_code: str = "POLAR.01.01.001",
    level4_label: str = "Alcohol protonation / deprotonation",
    signature: str = "O-H_to_N",
    pool: str = "proton_ion",
    reaction_smiles: str = "[CH3:1][O:2][H:3]>>[CH3:1][O-:2].[H+:3]",
):
    return {
        "case_id": case_id,
        "dataset_name": "SynEPD",
        "schema_version": "0.1.0",
        "level1_code": level1_code,
        "level1_name": level1_name,
        "level2_code": level2_code,
        "level2_name": level2_name,
        "level3_code": level3_code,
        "level3_name": level3_name,
        "level4_code": level4_code,
        "level4_label": level4_label,
        "case_variant": 1,
        "reaction_smiles": reaction_smiles,
        "reaction_center_signature": signature,
        "reaction_center_template_pool": pool,
        "reaction_center_uniqueness_scope": "within_level4",
        "shares_reaction_center_within_level4": False,
        "atom_mapping": {"mapped_atom_count": 3},
        "validation_status": "parse_pass_map_consistent_unique_rc",
        "curation_status": "template_seed_case",
        "manual_review_required": False,
    }


def _case(case_id: str, **kwargs) -> Case:
    return Case.from_dict(_raw_case(case_id, **kwargs))


def test_build_database_from_cases_indexes_hierarchy():
    cases = [
        _case("C1"),
        _case(
            "C2",
            level4_code="POLAR.01.01.002",
            level4_label="Phenol protonation / deprotonation",
            signature="N-H_to_O",
        ),
    ]
    db = build_database_from_cases(cases, summary={"case_count": 2})

    assert len(db) == 2
    assert db.require("C1").case_id == "C1"
    assert db.get("MISSING") is None
    assert db.case_count_by_level1() == {"POLAR": 2}
    assert len(db.by_level4) == 2
    assert db.level4_variant_counts()["POLAR.01.01.001"] == 1
    assert db.label_for_code("POLAR.01.01") == "Heteroatom proton transfer"
    assert [node.code for node in db.children("POLAR.01")] == ["POLAR.01.01"]
    assert db.duplicate_case_ids() == []


def test_construction_validation_rejects_duplicate_case_ids():
    cases = [_case("DUP"), _case("DUP")]

    report = validate_for_construction(cases)
    assert report.passed is False
    assert "Duplicate case_id" in report.errors[0]

    with pytest.raises(ConstructionValidationError):
        build_database_from_cases(cases)


def test_construction_validation_rejects_summary_mismatch():
    cases = [_case("C1")]

    with pytest.raises(ConstructionValidationError) as excinfo:
        build_database_from_cases(cases, summary={"case_count": 2})

    assert "summary case_count=2" in str(excinfo.value)


def test_query_helpers_filter_cases():
    cases = [
        _case("P1"),
        _case(
            "R1",
            level1_code="RADICAL",
            level1_name="Radical / single-electron / atom-transfer chemistry",
            level2_code="RADICAL.01",
            level2_name="Radical generation / initiation",
            level3_code="RADICAL.01.01",
            level3_name="Homolytic bond cleavage",
            level4_code="RADICAL.01.01.001",
            level4_label="C-Br homolysis",
            signature="homolysis",
            pool="rad_gen",
        ),
    ]
    db = build_database_from_cases(cases)

    assert by_level(db, 1, "POLAR") == (cases[0],)
    assert by_template_pool(db, "rad_gen") == (cases[1],)
    assert find_cases(db, level1="RADICAL", text="homolysis") == (cases[1],)
    assert Query(db).filter(level1="POLAR").filter(text="alcohol").count() == 1
    radical_matches = [node.code for node in search_labels(db, "radical")]
    assert radical_matches[:2] == ["RADICAL", "RADICAL.01"]
    assert "RADICAL.01.01.001" in radical_matches


def test_build_database_removes_unbalanced_reactions_by_default():
    charge_imbalanced = (
        "[CH3:1][C:2](=[N:3][CH3:4])[H:5].[C-:6]#[N:7]"
        ">>[CH3:1][C:2]([N:3][CH3:4])([H:5])[C:6]#[N:7]"
    )
    cases = [
        _case("GOOD"),
        _case("BAD-CHARGE", reaction_smiles=charge_imbalanced),
    ]

    db = build_database_from_cases(cases, summary={"case_count": len(cases)})

    assert len(db) == 1
    assert db.get("GOOD") is not None
    assert db.get("BAD-CHARGE") is None
    assert db.summary["case_count"] == 1
    assert db.summary["original_case_count"] == 2
    assert db.summary["construction_filter"]["removed_unbalanced_cases"] == 1


def test_sqlite_database_persists_and_queries(tmp_path):
    cases = [
        _case("C1"),
        _case(
            "C2",
            level4_code="POLAR.01.01.002",
            level4_label="Phenol protonation / deprotonation",
            signature="N-H_to_O",
        ),
    ]
    sqlite_path = tmp_path / "synepd.sqlite"

    db = build_sqlite_database_from_cases(
        cases,
        sqlite_path,
        summary={"case_count": 2},
    )
    db.close()

    with SQLiteSynEPDDatabase.connect(sqlite_path) as db:
        assert len(db) == 2
        assert db.summary["case_count"] == 2
        assert db.require("C1").case_id == "C1"
        assert db.get("MISSING") is None
        assert db.case_count_by_level1() == {"POLAR": 2}
        assert db.level4_variant_counts()["POLAR.01.01.001"] == 1
        assert db.label_for_code("POLAR.01.01") == "Heteroatom proton transfer"
        assert [node.code for node in db.children("POLAR.01")] == ["POLAR.01.01"]
        assert by_level(db, 4, "POLAR.01.01.002") == (cases[1],)
        assert by_template_pool(db, "proton_ion") == tuple(cases)
        assert find_cases(db, text="phenol") == (cases[1],)
        assert Query(db).filter(level1="POLAR").filter(text="alcohol").count() == 1
        assert [node.code for node in search_labels(db, "alcohol")] == [
            "POLAR.01.01.001"
        ]


def test_sqlite_construction_validation_runs_before_write(tmp_path):
    sqlite_path = tmp_path / "bad.sqlite"

    with pytest.raises(ConstructionValidationError):
        build_sqlite_database_from_cases([_case("DUP"), _case("DUP")], sqlite_path)

    assert not sqlite_path.exists()
