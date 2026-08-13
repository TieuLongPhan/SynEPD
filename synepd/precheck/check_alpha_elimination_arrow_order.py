"""Validate the defining arrow order of three-arrow alpha eliminations."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

ALPHA_ELIMINATION_PREFIX = "POLAR.05.04."
THREE_ARROW_SIGNATURE = Counter({"LP-/Sigma+": 1, "Sigma-/LP+": 2})


@dataclass(frozen=True)
class AlphaEliminationArrowOrderCheck:
    applicable: bool
    actual_arrow_types: tuple[str, ...]
    errors: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.errors


def check_alpha_elimination_arrow_order(
    record: dict[str, Any],
) -> AlphaEliminationArrowOrderCheck:
    """Require base attack to lead a three-arrow alpha elimination."""
    tax_codes = record.get("tax_codes") or [record.get("tax_code")]
    raw_epd = record.get("epd", [])
    if raw_epd is None:
        return AlphaEliminationArrowOrderCheck(
            False,
            (),
            ("EPD must be a list, not null",),
        )
    if not isinstance(raw_epd, (list, tuple)):
        return AlphaEliminationArrowOrderCheck(
            False,
            (),
            ("EPD must be a list",),
        )
    arrow_types = tuple(
        arrow[0] for arrow in raw_epd if isinstance(arrow, (list, tuple)) and arrow
    )
    applicable = (
        any(
            isinstance(code, str) and code.startswith(ALPHA_ELIMINATION_PREFIX)
            for code in tax_codes
        )
        and Counter(arrow_types) == THREE_ARROW_SIGNATURE
    )
    if not applicable:
        return AlphaEliminationArrowOrderCheck(False, arrow_types, ())
    errors = (
        ()
        if arrow_types[0] == "LP-/Sigma+"
        else ("base-to-proton bond formation must be the first arrow",)
    )
    return AlphaEliminationArrowOrderCheck(True, arrow_types, errors)
