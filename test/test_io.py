import json
import tempfile
from pathlib import Path
from synepd.io import load_cases, load_cases_jsonl, load_summary
from synepd.models import Case


def test_load_cases_and_jsonl():
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
        "reaction_center_uniqueness_scope": "within_level4",
        "shares_reaction_center_within_level4": False,
        "atom_mapping": {},
        "validation_status": "VALID",
        "curation_status": "APPROVED",
        "manual_review_required": False,
        "notes": "",
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        # Test full JSON loading
        json_path = Path(tmpdir) / "cases.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({"cases": [case_data]}, f)

        cases = load_cases(json_path)
        assert len(cases) == 1
        assert isinstance(cases[0], Case)
        assert cases[0].case_id == "polar01_001"

        # Test JSONL loading
        jsonl_path = Path(tmpdir) / "cases.jsonl"
        with open(jsonl_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(case_data) + "\n")

        cases_iter = list(load_cases_jsonl(jsonl_path))
        assert len(cases_iter) == 1
        assert cases_iter[0].case_id == "polar01_001"

        # Test summary loading
        summary_path = Path(tmpdir) / "summary.json"
        summary_data = {"version": "0.1.0", "count": 1}
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary_data, f)

        summary = load_summary(summary_path)
        assert summary["version"] == "0.1.0"
