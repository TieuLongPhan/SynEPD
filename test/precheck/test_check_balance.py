import pytest
from synepd.precheck.check_balance import check_reaction_balance


def test_check_reaction_balance():
    # Perfectly balanced reaction
    res = check_reaction_balance("[CH3:1][O:2][H:3]>>[CH3:1][O-:2].[H+:3]")
    assert res.balanced
    assert res.atom_count_balanced
    assert res.charge_balanced
    assert len(res.errors()) == 0

    # Charge imbalance
    res_bad_charge = check_reaction_balance("[CH3:1][O:2][H:3]>>[CH3:1][O-:2].[H:3]")
    assert not res_bad_charge.balanced
    assert res_bad_charge.atom_count_balanced
    assert not res_bad_charge.charge_balanced
    assert len(res_bad_charge.errors()) == 1

    # Atom count imbalance
    res_bad_atom = check_reaction_balance(
        "[CH3:1][O:2][H:3]>>[CH3:1][O-:2].[H+:3].[Cl-]"
    )
    assert not res_bad_atom.balanced
    assert not res_bad_atom.atom_count_balanced
    assert not res_bad_atom.charge_balanced
    assert len(res_bad_atom.errors()) == 2

    with pytest.raises(ValueError):
        check_reaction_balance("INVALID_SMILES")
