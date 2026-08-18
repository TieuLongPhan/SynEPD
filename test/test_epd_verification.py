import json
from collections import Counter
from pathlib import Path

from synepd.precheck.epd_verification import (
    _is_issue,
    verify_records,
    verify_records_strict,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_jones_endpoint_charge_correction_verifies_exactly():
    payload = json.loads(
        (REPOSITORY_ROOT / "data/polar.json").read_text(encoding="utf-8")
    )
    record = next(record for record in payload["records"] if record["id"] == 1538)

    result = verify_records([record])[0]

    assert result["status"] == "pass"
    assert result["matches_product"]
    assert result["structural_match"]
    assert result["charge_match"]
    assert result["smiles_match"]
    assert "[Cr-:23]" in result["rsmi"]
    assert "[Cr+:26]" in result["rsmi"]
    assert "epd_representation" not in result
    assert not _is_issue(result)


def test_only_mismatches_and_errors_are_strict_issues():
    assert not _is_issue({"status": "pass"})
    assert not _is_issue({"status": "surrogate_pass"})
    assert _is_issue({"status": "mismatch"})
    assert _is_issue({"status": "error"})


def test_strict_replay_rejects_pi_without_supporting_sigma():
    record = {
        "id": 1,
        "reaction_name": "Invalid sigma-first cleavage",
        "tax_code": "TEST",
        "rsmi": "[CH2:1]=[CH2:2]>>[CH2:1]=[CH2:2]",
        "epd": [["Sigma-/LP+", [1, 2], [1]]],
    }

    result = verify_records_strict([record])[0]

    assert result["status"] == "mismatch"
    assert "PI_WITHOUT_SIGMA" in result["issue_codes"]


def test_release_payload_contains_only_computational_representation_fields():
    payload = json.loads(
        (REPOSITORY_ROOT / "data/polar.json").read_text(encoding="utf-8")
    )
    records = payload["records"]

    representations = {
        record["id"]: record["epd_representation"]
        for record in records
        if "epd_representation" in record
    }

    assert set(representations) == {
        54,
        1478,
        1479,
        1480,
        1481,
        1483,
        1484,
        1485,
        1757,
        1846,
        1915,
    }
    assert all(
        set(representation) == {"mode", "closed_shell_atom_maps"}
        and representation["mode"] == "closed_shell_pair"
        and len(representation["closed_shell_atom_maps"]) == 1
        for representation in representations.values()
    )


def test_full_release_passes_strict_resource_aware_replay():
    payload = json.loads(
        (REPOSITORY_ROOT / "data/polar.json").read_text(encoding="utf-8")
    )

    results = verify_records_strict(payload["records"])

    failures = [result for result in results if _is_issue(result)]
    issue_codes = {
        issue_code for result in results for issue_code in result.get("issue_codes", [])
    }
    levels = Counter(result.get("verification_level") for result in results)
    assert failures == []
    assert "PI_WITHOUT_SIGMA" not in issue_codes
    assert levels == {"EXACT": 1913, "NORMALIZED": 11, "DELTA_CONSISTENT": 2}
