"""Atom map equality check for reaction SMILES."""

from __future__ import annotations

from dataclasses import dataclass
from rdkit import Chem


@dataclass(frozen=True)
class AtomMapBalance:
    """Check if the mapped atom indices are perfectly identical on both sides."""

    is_balanced: bool
    unmapped_reactants: set[int]
    unmapped_products: set[int]

    def errors(self) -> list[str]:
        errors = []
        if self.unmapped_reactants:
            errors.append(
                f"Mapped atoms missing in products: {sorted(self.unmapped_reactants)}"
            )
        if self.unmapped_products:
            errors.append(
                f"Mapped atoms missing in reactants: {sorted(self.unmapped_products)}"
            )
        return errors


def check_atom_map_balance(rsmi: str) -> AtomMapBalance:
    try:
        reactants, products = rsmi.split(">>")
    except ValueError:
        return AtomMapBalance(False, set(), set())

    def _get_map_nums(side: str) -> set[int]:
        mol = Chem.MolFromSmiles(side, sanitize=False)
        if not mol:
            return set()
        nums = set()
        for atom in mol.GetAtoms():
            if atom.HasProp("molAtomMapNumber"):
                nums.add(int(atom.GetProp("molAtomMapNumber")))
        return nums

    r_maps = _get_map_nums(reactants)
    p_maps = _get_map_nums(products)

    return AtomMapBalance(
        is_balanced=(r_maps == p_maps),
        unmapped_reactants=r_maps - p_maps,
        unmapped_products=p_maps - r_maps,
    )
