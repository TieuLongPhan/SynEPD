from synepd.precheck.check_carbocation_shift_adjacency import (
    check_carbocation_shift_adjacency,
)


def record(rsmi: str) -> dict[str, object]:
    return {
        "tax_code": "POLAR.07.01.016",
        "tax_codes": ["POLAR.07.01.016"],
        "rsmi": rsmi,
        "epd": [["Sigma-/Sigma+", [1, 3], [2, 3]]],
    }


def test_adjacent_1_2_shift_is_valid():
    result = check_carbocation_shift_adjacency(
        record("[C:1]([CH3:3])[CH2+:2]>>[C+:1][CH2:2][CH3:3]")
    )
    assert result.applicable
    assert result.valid
    assert result.migration_origin == 1
    assert result.cation_center == 2
    assert result.migrating_atom == 3


def test_nonlocal_shift_is_rejected():
    result = check_carbocation_shift_adjacency(
        record("[C:1]([CH3:3])[CH2:4][CH2+:2]>>[C+:1][CH2:4][CH2:2][CH3:3]")
    )
    assert result.applicable
    assert not result.valid
    assert any("not adjacent" in error for error in result.errors)


def test_other_arrow_families_are_not_applicable():
    candidate = record("[C:1]([CH3:3])[CH2+:2]>>[C+:1][CH2:2][CH3:3]")
    candidate["tax_codes"] = ["POLAR.03.01.001"]
    result = check_carbocation_shift_adjacency(candidate)
    assert not result.applicable
    assert result.valid
