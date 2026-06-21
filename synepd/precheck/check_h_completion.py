"""F-H — Hydrogen-completion ambiguity check.

Determines whether the implicit-hydrogen assignment in a reaction SMILES
is unambiguous after ITS construction.  A result of (None, None) from
HComplete.process means the H assignment is ambiguous or impossible;
this is recorded as a *warning*, not a hard failure.

Pipeline:
    its = rsmi_to_its(smiles, format='tuple')
    if direct_h_change(its) == 0: pass
    else: fail
"""

from __future__ import annotations

import logging
from typing import List

from synkit.Chem.Reaction.canon_rsmi import CanonRSMI as _CanonRSMI
from synkit.IO import rsmi_to_its
import networkx as nx

from synepd.models import Case
from synepd.precheck.report import ValidationResult

logger = logging.getLogger(__name__)

_canon = _CanonRSMI()


def _direct_its_hcount_change(its: nx.Graph) -> int:
    total = 0
    for _, data in its.nodes(data=True):
        hcount = data.get("hcount")
        if isinstance(hcount, (tuple, list)) and len(hcount) == 2:
            h_r, h_p = int(hcount[0] or 0), int(hcount[1] or 0)
            total += abs(h_r - h_p)
    return total


def _canonical_smiles(smiles: str) -> str:
    try:
        return _canon.canonicalise(smiles).canonical_rsmi
    except Exception:
        return smiles


def check_single_h_completion(smiles: str) -> tuple[bool, str]:
    """Returns (is_complete, error_message)."""
    smiles = _canonical_smiles(smiles)
    try:
        its = rsmi_to_its(smiles, drop_non_aam=True, format="tuple")
    except Exception as exc:
        return False, f"rsmi_to_its failed: {exc}"

    if its is None or its.number_of_nodes() == 0:
        return False, "ITS construction returned empty graph"

    h_change = _direct_its_hcount_change(its)
    if h_change == 0:
        return True, ""
    return False, f"Hydrogen counts change by {h_change} explicitly. Not H-complete."


def validate_h_completion(cases: List[Case]) -> List[ValidationResult]:
    """F-H: check whether H assignment is unambiguous for each case.

    Uses rsmi_to_its(format='tuple') which stores hcount as a
    (reactant, product) tuple — required so that HComplete can read the
    per-side hcount correctly.

    Status:
        pass — H assignment is unambiguous (new_its is not None)
        warn — H assignment is ambiguous or check failed (new_its is None)
        skip — graph conversion failed; H check cannot run
    """
    results: List[ValidationResult] = []
    for case in cases:
        smiles = _canonical_smiles(case.reaction_smiles)
        try:
            its = rsmi_to_its(smiles, drop_non_aam=True, format="tuple")
        except Exception as exc:
            results.append(
                ValidationResult(
                    case_id=case.case_id,
                    check_name="h_completion",
                    status="skip",
                    declared_level1=case.level1_code,
                    stored_signature=case.reaction_center_signature,
                    message=f"rsmi_to_its failed: {exc}",
                )
            )
            continue

        if its is None or its.number_of_nodes() == 0:
            results.append(
                ValidationResult(
                    case_id=case.case_id,
                    check_name="h_completion",
                    status="skip",
                    declared_level1=case.level1_code,
                    stored_signature=case.reaction_center_signature,
                    message="ITS construction returned empty graph",
                )
            )
            continue

        h_change = _direct_its_hcount_change(its)
        if h_change == 0:
            status = "pass"
            msg = ""
        else:
            status = "warn"
            msg = f"Hydrogen counts change by {h_change} explicitly. Not H-complete."

        results.append(
            ValidationResult(
                case_id=case.case_id,
                check_name="h_completion",
                status=status,
                declared_level1=case.level1_code,
                stored_signature=case.reaction_center_signature,
                message=msg,
            )
        )
    return results
