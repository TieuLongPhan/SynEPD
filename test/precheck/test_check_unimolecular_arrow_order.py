from synepd.precheck.check_unimolecular_arrow_order import (
    check_unimolecular_arrow_order,
)


def record(tax_code: str, arrow_types: list[str]) -> dict[str, object]:
    return {
        "tax_code": tax_code,
        "tax_codes": [tax_code],
        "epd": [[arrow_type, [1], [2]] for arrow_type in arrow_types],
    }


def test_sn1_ionization_must_precede_capture():
    result = check_unimolecular_arrow_order(
        record(
            "POLAR.02.02.001",
            ["Sigma-/LP+", "LP-/Sigma+", "LP-/Sigma+", "Sigma-/LP+"],
        )
    )
    assert result.valid

    reversed_result = check_unimolecular_arrow_order(
        record(
            "POLAR.02.02.001",
            ["LP-/Sigma+", "Sigma-/LP+", "LP-/Sigma+", "Sigma-/LP+"],
        )
    )
    assert not reversed_result.valid


def test_e1_ionization_must_precede_beta_deprotonation():
    result = check_unimolecular_arrow_order(
        record(
            "POLAR.05.02.003",
            ["Sigma-/LP+", "LP-/Sigma+", "Sigma-/Pi+"],
        )
    )
    assert result.valid

    reversed_result = check_unimolecular_arrow_order(
        record(
            "POLAR.05.02.003",
            ["Sigma-/Pi+", "LP-/Sigma+", "Sigma-/LP+"],
        )
    )
    assert not reversed_result.valid


def test_non_unimolecular_record_is_not_applicable():
    result = check_unimolecular_arrow_order(record("POLAR.02.01.001", ["LP-/Sigma+"]))
    assert not result.valid
    assert result.tax_code is None
