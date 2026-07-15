"""Validate curated arrow sequences for SN1- and E1-family records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# These stages encode the defining step order, not merely a preferred drawing
# order.  SN1 ionization must precede capture; E1 ionization must precede
# beta-deprotonation.  Proton transfers are split into their two EPD arrows.
EXPECTED_STAGES_BY_TAX_CODE: dict[str, tuple[tuple[str, str], ...]] = {
    **{
        code: (
            ("leaving-group ionization", "Sigma-/LP+"),
            ("nucleophile capture", "LP-/Sigma+"),
            ("base-to-proton bond formation", "LP-/Sigma+"),
            ("solvent O-H cleavage", "Sigma-/LP+"),
        )
        for code in (
            "POLAR.02.02.001",
            "POLAR.02.02.002",
            "POLAR.02.02.003",
            "POLAR.02.02.004",
            "POLAR.02.02.008",
        )
    },
    **{
        code: (
            ("alcohol protonation", "LP-/Sigma+"),
            ("hydrogen-halide cleavage", "Sigma-/LP+"),
            ("C-O ionization", "Sigma-/LP+"),
            ("halide capture", "LP-/Sigma+"),
        )
        for code in ("POLAR.02.02.005", "POLAR.02.02.012")
    },
    **{
        code: (("carbocation capture", "LP-/Sigma+"),)
        for code in (
            "POLAR.02.02.006",
            "POLAR.02.02.007",
            "POLAR.02.02.011",
        )
    },
    **{
        code: (
            ("nucleophile capture", "LP-/Sigma+"),
            ("cationic pi-bond shift", "Pi-/LP+"),
        )
        for code in ("POLAR.02.02.009", "POLAR.02.02.010")
    },
    **{
        code: (
            ("alcohol protonation", "LP-/Sigma+"),
            ("C-O ionization", "Sigma-/LP+"),
            ("beta-C-H elimination", "Sigma-/Pi+"),
        )
        for code in (
            "POLAR.05.02.001",
            "POLAR.05.02.002",
            "POLAR.05.02.005",
            "POLAR.05.02.006",
            "POLAR.05.02.007",
        )
    },
    "POLAR.05.02.003": (
        ("leaving-group ionization", "Sigma-/LP+"),
        ("base-to-beta-proton bond formation", "LP-/Sigma+"),
        ("beta-C-H elimination", "Sigma-/Pi+"),
    ),
    **{
        code: (
            ("base-to-beta-proton bond formation", "LP-/Sigma+"),
            ("beta-C-H elimination", "Sigma-/Pi+"),
        )
        for code in ("POLAR.05.02.004", "POLAR.05.02.008")
    },
}


@dataclass(frozen=True)
class UnimolecularArrowOrderCheck:
    """Comparison of a record's EPD sequence with its curated mechanism."""

    tax_code: str | None
    expected_stages: tuple[tuple[str, str], ...]
    actual_arrow_types: tuple[str, ...]
    errors: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.errors


def check_unimolecular_arrow_order(
    record: dict[str, Any],
) -> UnimolecularArrowOrderCheck:
    """Check an SN1/E1-family record against its required step sequence."""
    tax_codes = record.get("tax_codes") or [record.get("tax_code")]
    tax_code = next(
        (code for code in tax_codes if code in EXPECTED_STAGES_BY_TAX_CODE), None
    )
    actual_arrow_types = tuple(
        arrow[0]
        for arrow in record.get("epd", [])
        if isinstance(arrow, (list, tuple)) and arrow
    )
    if tax_code is None:
        return UnimolecularArrowOrderCheck(
            tax_code=None,
            expected_stages=(),
            actual_arrow_types=actual_arrow_types,
            errors=("record is not in a curated SN1/E1 mechanism family",),
        )

    expected_stages = EXPECTED_STAGES_BY_TAX_CODE[tax_code]
    expected_arrow_types = tuple(arrow_type for _, arrow_type in expected_stages)
    errors: list[str] = []
    if len(actual_arrow_types) != len(record.get("epd", [])):
        errors.append("one or more EPD arrows are malformed")
    if actual_arrow_types != expected_arrow_types:
        errors.append(
            "expected arrow types "
            f"{list(expected_arrow_types)}, got {list(actual_arrow_types)}"
        )
    return UnimolecularArrowOrderCheck(
        tax_code=tax_code,
        expected_stages=expected_stages,
        actual_arrow_types=actual_arrow_types,
        errors=tuple(errors),
    )
