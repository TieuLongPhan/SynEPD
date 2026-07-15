from synepd.core.representation import (
    remap_epd,
    remap_representation,
    representation_verification_rsmi,
)


def test_remaps_all_documented_representation_atom_maps():
    representation = {
        "mode": "closed_shell_surrogate",
        "chemical_product_fragment": "[O:25]=[Cr:23][O:29][Cr:26]=[O:28]",
        "lwg_product_fragment": "[O:25]=[Cr-:23][O:29][Cr+:26]=[O:28]",
        "chemical_oxidation_states": {"23": 3, "26": 3},
        "lwg_formal_charge_overrides": {"23": -1, "26": 1},
        "symmetry_equivalent_charge_overrides": {"23": 1, "26": -1},
        "unrepresented_electron_step": {
            "electron_count": 1,
            "source_atom": 23,
            "target_atom": 26,
        },
    }
    atom_map = {23: 16, 26: 17, 25: 9, 29: 12, 28: 11}

    remapped = remap_representation(
        representation, atom_map, namespace="canonical_aam_key"
    )

    assert remapped["chemical_oxidation_states"] == {"16": 3, "17": 3}
    assert remapped["lwg_formal_charge_overrides"] == {"16": -1, "17": 1}
    assert remapped["unrepresented_electron_step"]["source_atom"] == 16
    assert remapped["unrepresented_electron_step"]["target_atom"] == 17
    assert ":16]" in remapped["chemical_product_fragment"]
    assert ":17]" in remapped["lwg_product_fragment"]
    assert remapped["atom_map_namespace"] == "canonical_aam_key"
    assert representation["chemical_oxidation_states"] == {"23": 3, "26": 3}


def test_remap_epd_preserves_endpoint_cardinality():
    epd = [["Pi-/Sigma+", [26, 29], [23, 29]]]
    assert remap_epd(epd, {23: 16, 26: 17, 29: 12}) == [
        ["Pi-/Sigma+", [17, 12], [16, 12]]
    ]


def test_surrogate_verification_uses_charge_overrides_by_atom_map():
    rsmi = "[O:1]=[Cr:2][O:3][Cr:4]=[O:5]>>[O:1]=[Cr:2][O:3][Cr:4]=[O:5]"
    representation = {
        "mode": "closed_shell_surrogate",
        "lwg_formal_charge_overrides": {"2": -1, "4": 1},
    }

    verification_rsmi = representation_verification_rsmi(rsmi, representation)

    assert "[Cr-:2]" in verification_rsmi
    assert "[Cr+:4]" in verification_rsmi
