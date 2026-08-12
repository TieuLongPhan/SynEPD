import json
from pathlib import Path

from synepd.precheck.epd_verification import _is_issue, verify_records

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_jones_open_shell_endpoint_uses_documented_surrogate():
    payload = json.loads(
        (REPOSITORY_ROOT / "data/polar.json").read_text(encoding="utf-8")
    )
    record = next(record for record in payload["records"] if record["id"] == 1538)

    result = verify_records([record])[0]

    assert result["status"] == "surrogate_pass"
    assert result["matches_product"]
    assert result["structural_match"]
    assert result["charge_match"]
    assert result["smiles_match"]
    assert "[Cr:23]" in result["rsmi"]
    assert "[Cr-:23]" in result["verification_rsmi"]
    assert result["epd_representation"]["lwg_formal_charge_overrides"] == {
        "23": -1,
        "26": 1,
    }
    assert not _is_issue(result)


def test_only_mismatches_and_errors_are_strict_issues():
    assert not _is_issue({"status": "pass"})
    assert not _is_issue({"status": "surrogate_pass"})
    assert _is_issue({"status": "mismatch"})
    assert _is_issue({"status": "error"})


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

    assert set(representations) == {1538, 1915}
    assert all(
        set(representation) == {"mode", "lwg_formal_charge_overrides"}
        for representation in representations.values()
    )
