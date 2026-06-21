"""Validation result types and summary formatting."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ValidationResult:
    case_id: str
    check_name: str
    # "pass" | "fail" | "warn" | "skip"
    status: str
    declared_level1: str
    stored_signature: str
    message: str = ""
    computed_wl_hash: Optional[str] = None
    predicted_level1: Optional[str] = None
    level1_consistent: Optional[bool] = None
    details: Dict[str, Any] = field(default_factory=dict)


def format_summary(results: List[ValidationResult]) -> Dict[str, Any]:
    """Aggregate counts by check name and status."""
    by_check: Dict[str, Counter] = {}
    for r in results:
        by_check.setdefault(r.check_name, Counter())[r.status] += 1

    total = len(results)
    return {
        "total": total,
        "passed": sum(1 for r in results if r.status == "pass"),
        "failed": sum(1 for r in results if r.status == "fail"),
        "warned": sum(1 for r in results if r.status == "warn"),
        "by_check": {k: dict(v) for k, v in by_check.items()},
    }
