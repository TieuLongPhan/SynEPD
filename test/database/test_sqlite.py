import tempfile
from pathlib import Path
from synepd.models import Case
from synepd.database.sqlite import SQLiteSynEPDDatabase
from synepd.construct.builder import build_sqlite_database_from_cases


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


def test_sqlite_synepd_database():
    case = _case("C1")
    with tempfile.TemporaryDirectory() as tmpdir:
        sqlite_path = Path(tmpdir) / "test.sqlite"
        db = build_sqlite_database_from_cases(
            (case,), sqlite_path, summary={"case_count": 1}
        )
        db.close()

        with SQLiteSynEPDDatabase.connect(sqlite_path) as db:
            assert len(db) == 1
            assert db.summary["case_count"] == 1
            assert db.require("C1").case_id == "C1"
            assert db.get("MISSING") is None
            assert db.case_count_by_level1() == {"POLAR": 1}
            assert db.level4_variant_counts() == {"POLAR.01.01.001": 1}
