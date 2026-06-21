"""Pre-database validation checks used during construction."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from synepd.models import Case
from synepd.precheck import (
    check_reaction_balance,
)


class ConstructionValidationError(ValueError):
    """Raised when cases fail validation before database construction."""

    def __init__(self, report: "ConstructionValidationReport") -> None:
        self.report = report
        super().__init__(report.message())


@dataclass(frozen=True)
class ConstructionValidationReport:
    """Result of validating cases before database construction."""

    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    details: Mapping[str, object] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """Return true when no blocking errors were found."""
        return not self.errors

    def message(self) -> str:
        """Return a compact human-readable failure message."""
        if self.passed:
            return "Construction validation passed"
        return "Construction validation failed: " + "; ".join(self.errors)


def validate_for_construction(
    cases: Iterable[Case],
    summary: Mapping[str, object] | None = None,
) -> ConstructionValidationReport:
    """Validate cases before they are admitted into ``SynEPDDatabase``.

    These checks are intentionally fast and structural. Chemistry-heavy checks
    such as RC extraction stay in ``synepd.precheck`` and can be run as audit
    jobs; construction only guards invariants required for reliable indexing.
    """
    case_tuple = tuple(cases)
    errors: list[str] = []
    warnings: list[str] = []
    details: dict[str, object] = {"case_count": len(case_tuple)}

    if not case_tuple:
        errors.append("No cases were provided")

    duplicates = _duplicate_case_ids(case_tuple)
    if duplicates:
        errors.append(f"Duplicate case_id values: {duplicates[:10]}")
    details["duplicate_case_ids"] = duplicates

    if summary:
        errors.extend(_summary_errors(case_tuple, summary))

    return ConstructionValidationReport(
        errors=tuple(errors),
        warnings=tuple(warnings),
        details=details,
    )


def ensure_valid_for_construction(
    cases: Iterable[Case],
    summary: Mapping[str, object] | None = None,
) -> tuple[Case, ...]:
    """Return cases as a tuple or raise ``ConstructionValidationError``."""
    case_tuple = tuple(cases)
    report = validate_for_construction(case_tuple, summary=summary)
    if not report.passed:
        raise ConstructionValidationError(report)
    return case_tuple


def filter_balanced_cases(cases: Iterable[Case]) -> tuple[Case, ...]:
    """Return only cases with balanced atom counts and formal charges."""
    kept: list[Case] = []
    for case in cases:
        try:
            if check_reaction_balance(case.reaction_smiles).balanced:
                kept.append(case)
        except Exception:
            continue
    return tuple(kept)


def reaction_balance_failures(cases: Iterable[Case]) -> list[dict[str, object]]:
    """Return compact balance-failure records for removed cases."""
    failures: list[dict[str, object]] = []
    for case in cases:
        try:
            balance = check_reaction_balance(case.reaction_smiles)
        except Exception as exc:
            failures.append(
                {
                    "case_id": case.case_id,
                    "error": f"Reaction-balance check failed: {exc}",
                }
            )
            continue
        if not balance.balanced:
            failures.append(
                {
                    "case_id": case.case_id,
                    "atom_count_balanced": balance.atom_count_balanced,
                    "charge_balanced": balance.charge_balanced,
                    "reactant_atom_count": balance.reactant_atom_count,
                    "product_atom_count": balance.product_atom_count,
                    "reactant_formal_charge": balance.reactant_formal_charge,
                    "product_formal_charge": balance.product_formal_charge,
                    "errors": balance.errors(),
                }
            )
    return failures


def _duplicate_case_ids(cases: Sequence[Case]) -> list[str]:
    counts = Counter(case.case_id for case in cases)
    return sorted(case_id for case_id, count in counts.items() if count > 1)


def _summary_errors(cases: Sequence[Case], summary: Mapping[str, object]) -> list[str]:
    errors: list[str] = []

    expected_case_count = summary.get("case_count")
    if expected_case_count is not None and int(expected_case_count) != len(cases):
        errors.append(
            f"summary case_count={expected_case_count} does not match "
            f"loaded cases={len(cases)}"
        )

    by_level1 = summary.get("by_level1")
    if isinstance(by_level1, Mapping):
        actual = Counter(case.level1_code for case in cases)
        expected = {str(key): int(value) for key, value in by_level1.items()}
        if dict(actual) != expected:
            errors.append(
                f"summary by_level1={expected} does not match loaded {dict(actual)}"
            )

    expected_level4_count = summary.get("level4_count")
    actual_level4_counts = Counter(case.level4_code for case in cases)
    if expected_level4_count is not None and int(expected_level4_count) != len(
        actual_level4_counts
    ):
        errors.append(
            f"summary level4_count={expected_level4_count} does not match "
            f"loaded level4_count={len(actual_level4_counts)}"
        )

    cases_per_level4 = summary.get("cases_per_level4")
    if cases_per_level4 is not None:
        expected_per_level4 = int(cases_per_level4)
        bad_counts = {
            code: count
            for code, count in actual_level4_counts.items()
            if count != expected_per_level4
        }
        if bad_counts:
            sample = dict(list(sorted(bad_counts.items()))[:10])
            errors.append(
                f"Level-4 case counts differ from {expected_per_level4}: {sample}"
            )

    return errors
