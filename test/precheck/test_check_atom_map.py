from synepd.precheck.check_atom_map import check_atom_map_balance


def test_check_atom_map_balance():
    # Perfectly balanced map
    res = check_atom_map_balance("[CH3:1][OH:2]>>[CH3:1][O-:2].[H+]")
    assert res.is_balanced
    assert len(res.errors()) == 0

    # Unbalanced map (atom map missing in products)
    res_bad1 = check_atom_map_balance("[CH3:1][OH:2]>>[CH3:1][OH]")
    assert not res_bad1.is_balanced
    assert 2 in res_bad1.unmapped_reactants
    assert len(res_bad1.errors()) == 1

    # Unbalanced map (atom map missing in reactants)
    res_bad2 = check_atom_map_balance("[CH3][OH]>>[CH3:1][OH]")
    assert not res_bad2.is_balanced
    assert 1 in res_bad2.unmapped_products
    assert len(res_bad2.errors()) == 1
