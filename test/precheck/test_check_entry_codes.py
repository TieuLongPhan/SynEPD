from synepd.precheck.check_entry_codes import check_entry_code_uniqueness
import pytest


def test_primary_entry_code_collisions_are_reported():
    records = [
        {"id": 1, "entry_code": "POLAR.01.01.001.001"},
        {"id": 2, "entry_code": "POLAR.01.01.001.001"},
    ]
    result = check_entry_code_uniqueness(records)
    assert not result.valid
    assert result.collisions == {"POLAR.01.01.001.001": (1, 2)}


def test_alias_collisions_are_optional():
    records = [
        {
            "id": 1,
            "entry_code": "POLAR.01.01.001.001",
            "entry_codes": ["POLAR.01.01.001.001", "POLAR.02.01.001.001"],
        },
        {
            "id": 2,
            "entry_code": "POLAR.02.01.001.001",
            "entry_codes": ["POLAR.02.01.001.001"],
        },
    ]
    assert check_entry_code_uniqueness(records).valid
    result = check_entry_code_uniqueness(records, include_aliases=True)
    assert result.collisions == {"POLAR.02.01.001.001": (1, 2)}


def test_aliases_must_contain_primary_exactly_once():
    omitted = [
        {
            "id": 1,
            "entry_code": "POLAR.01.01.001.001",
            "entry_codes": ["POLAR.01.01.001.002"],
        }
    ]
    duplicated = [
        {
            "id": 1,
            "entry_code": "POLAR.01.01.001.001",
            "entry_codes": [
                "POLAR.01.01.001.001",
                "POLAR.01.01.001.001",
            ],
        }
    ]

    with pytest.raises(ValueError, match="exactly once"):
        check_entry_code_uniqueness(omitted, include_aliases=True)
    with pytest.raises(ValueError, match="exactly once"):
        check_entry_code_uniqueness(duplicated, include_aliases=True)


def test_aliases_must_be_a_list_of_nonempty_strings():
    wrong_type = [
        {
            "id": 1,
            "entry_code": "POLAR.01.01.001.001",
            "entry_codes": "POLAR.01.01.001.001",
        }
    ]
    invalid_element = [
        {
            "id": 1,
            "entry_code": "POLAR.01.01.001.001",
            "entry_codes": ["POLAR.01.01.001.001", []],
        }
    ]

    with pytest.raises(ValueError, match="must be a list"):
        check_entry_code_uniqueness(wrong_type, include_aliases=True)
    with pytest.raises(ValueError, match="invalid entry code"):
        check_entry_code_uniqueness(invalid_element, include_aliases=True)
