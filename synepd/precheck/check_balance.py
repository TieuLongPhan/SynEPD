"""Stoichiometric and charge-balance checks for reaction SMILES."""

from __future__ import annotations

from dataclasses import dataclass
from rdkit import Chem


@dataclass(frozen=True)
class ReactionBalance:
    """Atom-count and formal-charge balance for one reaction SMILES."""

    atom_count_balanced: bool
    charge_balanced: bool
    reactant_atom_count: int
    product_atom_count: int
    reactant_formal_charge: int
    product_formal_charge: int

    @property
    def balanced(self) -> bool:
        """Return true when atom count and formal charge are both balanced."""
        return self.atom_count_balanced and self.charge_balanced

    def errors(self) -> list[str]:
        """Return human-readable balance errors."""
        errors: list[str] = []
        if not self.atom_count_balanced:
            errors.append(
                "Atom-count imbalance: "
                f"reactants={self.reactant_atom_count}, "
                f"products={self.product_atom_count}"
            )
        if not self.charge_balanced:
            errors.append(
                "Formal-charge imbalance: "
                f"reactants={self.reactant_formal_charge}, "
                f"products={self.product_formal_charge}"
            )
        return errors


def check_reaction_balance(rsmi: str) -> ReactionBalance:
    """Check total atom count and formal charge on both reaction sides."""
    try:
        reactants, products = rsmi.split(">>")
    except ValueError as exc:
        raise ValueError("Expected reaction SMILES with one '>>' separator") from exc

    reactant_atoms, reactant_charge = _side_balance(reactants)
    product_atoms, product_charge = _side_balance(products)
    return ReactionBalance(
        atom_count_balanced=reactant_atoms == product_atoms,
        charge_balanced=reactant_charge == product_charge,
        reactant_atom_count=reactant_atoms,
        product_atom_count=product_atoms,
        reactant_formal_charge=reactant_charge,
        product_formal_charge=product_charge,
    )


def _side_balance(side: str) -> tuple[int, int]:
    atom_count = 0
    formal_charge = 0
    fragments = side.split(".")
    if not fragments:
        raise ValueError("Reaction side has no fragments")
    for fragment in fragments:
        if not fragment:
            raise ValueError("Reaction side contains an empty fragment")
        mol = Chem.MolFromSmiles(fragment, sanitize=False)
        if mol is None:
            raise ValueError(f"Could not parse fragment: {fragment}")
        atom_count += mol.GetNumAtoms()
        formal_charge += sum(atom.GetFormalCharge() for atom in mol.GetAtoms())
    return atom_count, formal_charge
