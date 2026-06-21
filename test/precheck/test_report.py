from synepd.precheck.report import ValidationResult, format_summary


def test_format_summary():
    results = [
        ValidationResult("polar01_001", "check1", "pass", "POLAR", "SIGMA"),
        ValidationResult(
            "polar01_002", "check1", "fail", "POLAR", "SIGMA", "Failed check"
        ),
        ValidationResult(
            "polar01_003", "check2", "warn", "POLAR", "SIGMA", "Warning check"
        ),
    ]
    summary = format_summary(results)
    assert summary["total"] == 3
    assert summary["passed"] == 1
    assert summary["failed"] == 1
    assert summary["warned"] == 1
    assert summary["by_check"]["check1"] == {"pass": 1, "fail": 1}
    assert summary["by_check"]["check2"] == {"warn": 1}
