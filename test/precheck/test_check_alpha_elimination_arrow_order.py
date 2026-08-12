from synepd.precheck.check_alpha_elimination_arrow_order import (
    check_alpha_elimination_arrow_order,
)


def record(arrow_types: list[str]) -> dict[str, object]:
    return {
        "tax_code": "POLAR.05.04.001",
        "tax_codes": ["POLAR.05.04.001"],
        "epd": [[arrow_type, [1], [2]] for arrow_type in arrow_types],
    }


def test_base_arrow_must_lead_three_arrow_alpha_elimination():
    valid = check_alpha_elimination_arrow_order(
        record(["LP-/Sigma+", "Sigma-/LP+", "Sigma-/LP+"])
    )
    assert valid.applicable
    assert valid.valid

    reversed_result = check_alpha_elimination_arrow_order(
        record(["Sigma-/LP+", "Sigma-/LP+", "LP-/Sigma+"])
    )
    assert reversed_result.applicable
    assert not reversed_result.valid


def test_preformed_anion_alpha_elimination_is_not_applicable():
    result = check_alpha_elimination_arrow_order(record(["Sigma-/LP+"]))
    assert not result.applicable
    assert result.valid


def test_null_epd_is_reported_as_invalid():
    candidate = record([])
    candidate["epd"] = None
    result = check_alpha_elimination_arrow_order(candidate)
    assert not result.applicable
    assert not result.valid
    assert result.errors == ("EPD must be a list, not null",)
