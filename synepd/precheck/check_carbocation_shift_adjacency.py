"""Validate adjacency for one-arrow 1,2 carbocation migrations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rdkit import Chem


CARBOCATION_REARRANGEMENT_PREFIX = "POLAR.07.01."


@dataclass(frozen=True)
class CarbocationShiftAdjacencyCheck:
    """Result for a candidate one-arrow sigma-bond migration."""

    applicable: bool
    migration_origin: int | None
    cation_center: int | None
    migrating_atom: int | None
    errors: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.errors


def check_carbocation_shift_adjacency(
    record: dict[str, Any],
) -> CarbocationShiftAdjacencyCheck:
    """Require the migration origin to neighbor the accepting cation center.

    A one-step Wagner–Meerwein/1,2 shift has an EPD of the form
    ``Sigma-/Sigma+: (origin,migrant) -> (cation,migrant)``.  The origin and
    cation center must already be bonded in the reactant graph; otherwise the
    drawing encodes a nonlocal 1,3/1,4 migration.
    """
    tax_codes = record.get("tax_codes") or [record.get("tax_code")]
    epd = record.get("epd") or []
    applicable = (
        any(
            isinstance(code, str)
            and code.startswith(CARBOCATION_REARRANGEMENT_PREFIX)
            for code in tax_codes
        )
        and len(epd) == 1
        and isinstance(epd[0], (list, tuple))
        and len(epd[0]) == 3
        and epd[0][0] == "Sigma-/Sigma+"
    )
    if not applicable:
        return CarbocationShiftAdjacencyCheck(False, None, None, None, ())

    source = epd[0][1]
    target = epd[0][2]
    errors: list[str] = []
    if not (
        isinstance(source, (list, tuple))
        and isinstance(target, (list, tuple))
        and len(source) == 2
        and len(target) == 2
        and all(isinstance(value, int) for value in (*source, *target))
    ):
        return CarbocationShiftAdjacencyCheck(
            True, None, None, None, ("migration endpoints must be two atom-map pairs",)
        )

    shared = set(source) & set(target)
    if len(shared) != 1:
        return CarbocationShiftAdjacencyCheck(
            True,
            None,
            None,
            None,
            ("source and target bonds must share exactly the migrating atom",),
        )
    migrating_atom = shared.pop()
    migration_origin = next(value for value in source if value != migrating_atom)
    cation_center = next(value for value in target if value != migrating_atom)

    rsmi = record.get("rsmi")
    if not isinstance(rsmi, str) or ">>" not in rsmi:
        errors.append("record has no valid reaction SMILES")
    else:
        reactants = rsmi.split(">>", 1)[0]
        mol = Chem.MolFromSmiles(reactants, sanitize=False)
        if mol is None:
            errors.append("reactant SMILES could not be parsed")
        else:
            by_map = {
                atom.GetAtomMapNum(): atom.GetIdx()
                for atom in mol.GetAtoms()
                if atom.GetAtomMapNum()
            }
            missing = sorted(
                {migration_origin, cation_center, migrating_atom} - by_map.keys()
            )
            if missing:
                errors.append(f"reactant is missing atom maps {missing}")
            else:
                if mol.GetBondBetweenAtoms(
                    by_map[migration_origin], by_map[migrating_atom]
                ) is None:
                    errors.append("the migrating sigma bond is absent in reactants")
                if mol.GetBondBetweenAtoms(
                    by_map[migration_origin], by_map[cation_center]
                ) is None:
                    errors.append(
                        "migration origin and accepting cation center are not adjacent"
                    )
                cation = mol.GetAtomWithIdx(by_map[cation_center])
                if cation.GetFormalCharge() <= 0:
                    errors.append("the accepting reactant atom is not cationic")

    return CarbocationShiftAdjacencyCheck(
        True,
        migration_origin,
        cation_center,
        migrating_atom,
        tuple(errors),
    )
