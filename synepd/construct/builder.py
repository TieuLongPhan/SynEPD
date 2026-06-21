"""Database construction helpers for SynEPD exports."""

from __future__ import annotations

from pathlib import Path
from collections import Counter
from typing import Iterable, Mapping

from synepd.construct.checks import (
    ensure_valid_for_construction,
    filter_balanced_cases,
    reaction_balance_failures,
)
from synepd.database import SQLiteSynEPDDatabase, SynEPDDatabase, write_sqlite_database
from synepd.io import load_cases, load_cases_jsonl, load_summary
from synepd.models import Case


def default_data_paths(root: Path | str | None = None) -> dict[str, Path]:
    """Return conventional data export paths for a repository root."""
    base = Path(root) if root is not None else Path.cwd()
    data = base / "data"
    return {
        "cases_full": data / "synepd_v0_1_0_cases_full.json",
        "cases_jsonl": data / "synepd_v0_1_0_cases.jsonl",
        "summary": data / "synepd_v0_1_0_summary.json",
        "hierarchy_tree": data / "synepd_v0_1_0_hierarchy_tree.md",
        "sqlite": data / "synepd_v0_1_0.sqlite",
    }


def build_database_from_cases(
    cases: Iterable[Case],
    summary: Mapping[str, object] | None = None,
    *,
    validate: bool = True,
    remove_unbalanced: bool = True,
) -> SynEPDDatabase:
    """Build an indexed database from already-loaded cases.

    By default, structural construction checks must pass before any
    ``SynEPDDatabase`` is created.
    """
    summary = summary or {}
    case_tuple = tuple(cases)
    if remove_unbalanced:
        case_tuple, summary = _remove_unbalanced_cases(case_tuple, summary)
    if validate:
        case_tuple = ensure_valid_for_construction(case_tuple, summary=summary)
    return SynEPDDatabase(cases=case_tuple, summary=summary)


def build_database_from_paths(
    cases_path: Path | str,
    summary_path: Path | str | None = None,
    *,
    validate: bool = True,
    remove_unbalanced: bool = True,
) -> SynEPDDatabase:
    """Build an indexed database from a JSON, JSON.GZ, or JSONL export."""
    cases_path = Path(cases_path)
    if cases_path.suffix == ".jsonl":
        cases = tuple(load_cases_jsonl(cases_path))
    else:
        cases = tuple(load_cases(cases_path))

    summary: Mapping[str, object] = {}
    if summary_path is not None:
        summary = load_summary(summary_path)

    return build_database_from_cases(
        cases,
        summary=summary,
        validate=validate,
        remove_unbalanced=remove_unbalanced,
    )


def build_database(
    root: Path | str | None = None,
    *,
    validate: bool = True,
    remove_unbalanced: bool = True,
) -> SynEPDDatabase:
    """Build the database from the conventional repo ``data/`` paths."""
    paths = default_data_paths(root)
    return build_database_from_paths(
        paths["cases_full"],
        paths["summary"],
        validate=validate,
        remove_unbalanced=remove_unbalanced,
    )


def build_sqlite_database_from_cases(
    cases: Iterable[Case],
    sqlite_path: Path | str,
    summary: Mapping[str, object] | None = None,
    *,
    validate: bool = True,
    overwrite: bool = True,
    remove_unbalanced: bool = True,
) -> SQLiteSynEPDDatabase:
    """Build a validated SQLite database from already-loaded cases."""
    summary = summary or {}
    case_tuple = tuple(cases)
    if remove_unbalanced:
        case_tuple, summary = _remove_unbalanced_cases(case_tuple, summary)
    if validate:
        case_tuple = ensure_valid_for_construction(case_tuple, summary=summary)
    return write_sqlite_database(
        sqlite_path,
        case_tuple,
        summary=summary,
        overwrite=overwrite,
    )


def build_sqlite_database_from_paths(
    cases_path: Path | str,
    sqlite_path: Path | str,
    summary_path: Path | str | None = None,
    *,
    validate: bool = True,
    overwrite: bool = True,
    remove_unbalanced: bool = True,
) -> SQLiteSynEPDDatabase:
    """Build a validated SQLite database from a JSON, JSON.GZ, or JSONL export."""
    cases_path = Path(cases_path)
    if cases_path.suffix == ".jsonl":
        cases = tuple(load_cases_jsonl(cases_path))
    else:
        cases = tuple(load_cases(cases_path))

    summary: Mapping[str, object] = {}
    if summary_path is not None:
        summary = load_summary(summary_path)

    return build_sqlite_database_from_cases(
        cases,
        sqlite_path,
        summary=summary,
        validate=validate,
        overwrite=overwrite,
        remove_unbalanced=remove_unbalanced,
    )


def build_sqlite_database(
    root: Path | str | None = None,
    sqlite_path: Path | str | None = None,
    *,
    validate: bool = True,
    overwrite: bool = True,
    remove_unbalanced: bool = True,
) -> SQLiteSynEPDDatabase:
    """Build the conventional repo data export into a SQLite database."""
    paths = default_data_paths(root)
    return build_sqlite_database_from_paths(
        paths["cases_full"],
        sqlite_path or paths["sqlite"],
        paths["summary"],
        validate=validate,
        overwrite=overwrite,
        remove_unbalanced=remove_unbalanced,
    )


def _remove_unbalanced_cases(
    cases: tuple[Case, ...],
    summary: Mapping[str, object],
) -> tuple[tuple[Case, ...], Mapping[str, object]]:
    failures = reaction_balance_failures(cases)
    if not failures:
        return cases, summary

    kept = filter_balanced_cases(cases)
    adjusted = _filtered_summary(summary, kept, original_count=len(cases))
    adjusted["construction_filter"] = {
        "removed_unbalanced_cases": len(cases) - len(kept),
        "checks": ["atom_count_balanced", "formal_charge_balanced"],
        "sample_failures": failures[:10],
    }
    return kept, adjusted


def _filtered_summary(
    summary: Mapping[str, object],
    cases: tuple[Case, ...],
    *,
    original_count: int,
) -> dict[str, object]:
    adjusted = dict(summary)
    level1_counts = Counter(case.level1_code for case in cases)
    adjusted["case_count"] = len(cases)
    adjusted["original_case_count"] = original_count
    adjusted["by_level1"] = dict(sorted(level1_counts.items()))
    adjusted["level4_count"] = len({case.level4_code for case in cases})
    adjusted.pop("cases_per_level4", None)
    return adjusted
