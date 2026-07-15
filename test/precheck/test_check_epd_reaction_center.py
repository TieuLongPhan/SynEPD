from synepd.precheck.check_epd_reaction_center import check_epd_reaction_center

RSMI = "[CH3:1][Br:2].[OH-:3]>>[CH3:1][OH:3].[Br-:2]"


def test_epd_atom_maps_match_reaction_center():
    result = check_epd_reaction_center(RSMI, [["LP-/Sigma+", [3], [1, 2]]])

    assert result.matches
    assert result.covers_reaction_center
    assert result.epd_atom_maps == frozenset({1, 2, 3})
    assert result.reaction_center_atom_maps == frozenset({1, 2, 3})


def test_epd_atom_maps_report_wrong_and_missing_center_maps():
    result = check_epd_reaction_center(RSMI, [["LP-/Sigma+", [3], [1, 9]]])

    assert not result.matches
    assert not result.covers_reaction_center
    assert result.maps_not_in_reaction_center == frozenset({9})
    assert result.reaction_center_maps_not_in_epd == frozenset({2})


def test_epd_context_atoms_are_allowed_when_center_is_covered():
    result = check_epd_reaction_center(RSMI, [["LP-/Sigma+", [3], [1, 2, 9]]])

    assert result.covers_reaction_center
    assert not result.matches
    assert result.maps_not_in_reaction_center == frozenset({9})


def test_epd_atom_maps_report_invalid_epd_shape():
    result = check_epd_reaction_center(RSMI, [["LP-/Sigma+", [3]]])

    assert not result.matches
    assert result.errors == ("EPD arrow 1 must be [type, source, target]",)
