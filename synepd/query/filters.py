"""Composable query helpers for SynEPD databases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Tuple

from synepd.database import HierarchyNode, SQLiteSynEPDDatabase, SynEPDDatabase
from synepd.models import Case

Database = SynEPDDatabase | SQLiteSynEPDDatabase


def by_level(db: Database, level: int, code: str) -> Tuple[Case, ...]:
    """Return cases for a specific hierarchy level and code."""
    if isinstance(db, SQLiteSynEPDDatabase):
        queries = {
            1: {"level1": code},
            2: {"level2": code},
            3: {"level3": code},
            4: {"level4": code},
        }
        if level not in queries:
            raise ValueError(f"Unsupported hierarchy level: {level}")
        return db.query_cases(**queries[level])

    index = {
        1: db.by_level1,
        2: db.by_level2,
        3: db.by_level3,
        4: db.by_level4,
    }.get(level)
    if index is None:
        raise ValueError(f"Unsupported hierarchy level: {level}")
    return index.get(code, ())


def by_template_pool(db: Database, pool: str) -> Tuple[Case, ...]:
    """Return cases for a reaction-center template pool."""
    if isinstance(db, SQLiteSynEPDDatabase):
        return db.query_cases(template_pool=pool)
    return db.by_template_pool.get(pool, ())


def find_cases(
    db: Database,
    *,
    level1: str | None = None,
    level2: str | None = None,
    level3: str | None = None,
    level4: str | None = None,
    template_pool: str | None = None,
    signature: str | None = None,
    text: str | None = None,
) -> Tuple[Case, ...]:
    """Filter cases by hierarchy fields, template metadata, and label text."""
    if isinstance(db, SQLiteSynEPDDatabase):
        return db.query_cases(
            level1=level1,
            level2=level2,
            level3=level3,
            level4=level4,
            template_pool=template_pool,
            signature=signature,
            text=text,
        )

    cases: Iterable[Case] = db.cases
    if level1 is not None:
        cases = (case for case in cases if case.level1_code == level1)
    if level2 is not None:
        cases = (case for case in cases if case.level2_code == level2)
    if level3 is not None:
        cases = (case for case in cases if case.level3_code == level3)
    if level4 is not None:
        cases = (case for case in cases if case.level4_code == level4)
    if template_pool is not None:
        cases = (
            case
            for case in cases
            if case.reaction_center_template_pool == template_pool
        )
    if signature is not None:
        cases = (case for case in cases if case.reaction_center_signature == signature)
    if text is not None:
        needle = text.casefold()
        cases = (
            case
            for case in cases
            if needle in case.level4_label.casefold()
            or needle in case.reaction_smiles.casefold()
        )
    return tuple(cases)


def search_labels(db: Database, text: str) -> Tuple[HierarchyNode, ...]:
    """Search hierarchy labels by case-insensitive text."""
    if isinstance(db, SQLiteSynEPDDatabase):
        return db.search_labels(text)

    needle = text.casefold()
    matches = [
        node
        for node in db.hierarchy.values()
        if needle in node.name.casefold() or needle in node.code.casefold()
    ]
    return tuple(sorted(matches, key=lambda node: (node.level, node.code)))


@dataclass(frozen=True)
class Query:
    """Small fluent wrapper around ``find_cases``."""

    db: Database
    cases: Tuple[Case, ...] | None = None

    def __post_init__(self) -> None:
        if self.cases is None:
            object.__setattr__(self, "cases", self.db.cases)

    def filter(
        self,
        *,
        level1: str | None = None,
        level2: str | None = None,
        level3: str | None = None,
        level4: str | None = None,
        template_pool: str | None = None,
        signature: str | None = None,
        text: str | None = None,
    ) -> "Query":
        tmp_db = SynEPDDatabase(cases=self.cases or (), summary=self.db.summary)
        cases = find_cases(
            tmp_db,
            level1=level1,
            level2=level2,
            level3=level3,
            level4=level4,
            template_pool=template_pool,
            signature=signature,
            text=text,
        )
        return Query(self.db, cases=cases)

    def all(self) -> Tuple[Case, ...]:
        """Return matched cases."""
        return self.cases or ()

    def count(self) -> int:
        """Return matched case count."""
        return len(self.all())
