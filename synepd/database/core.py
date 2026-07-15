"""In-memory indexed database for SynEPD case records."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

from synepd.models import Case


@dataclass(frozen=True)
class HierarchyNode:
    """One mechanism hierarchy node inferred from case records."""

    code: str
    name: str
    level: int
    parent_code: str | None = None
    case_count: int = 0


@dataclass
class CaseIndex:
    """Indexed view of SynEPD cases."""

    cases: Tuple[Case, ...]
    summary: Mapping[str, object] = field(default_factory=dict)
    hierarchy: Mapping[str, HierarchyNode] = field(default_factory=dict)
    by_case_id: Mapping[str, Case] = field(init=False)
    by_level1: Mapping[str, Tuple[Case, ...]] = field(init=False)
    by_level2: Mapping[str, Tuple[Case, ...]] = field(init=False)
    by_level3: Mapping[str, Tuple[Case, ...]] = field(init=False)
    by_level4: Mapping[str, Tuple[Case, ...]] = field(init=False)
    by_signature: Mapping[str, Tuple[Case, ...]] = field(init=False)
    by_template_pool: Mapping[str, Tuple[Case, ...]] = field(init=False)

    def __post_init__(self) -> None:
        cases = tuple(self.cases)
        self.cases = cases
        self.by_case_id = {case.case_id: case for case in cases}
        self.by_level1 = _group_cases(cases, "level1_code")
        self.by_level2 = _group_cases(cases, "level2_code")
        self.by_level3 = _group_cases(cases, "level3_code")
        self.by_level4 = _group_cases(cases, "level4_code")
        self.by_signature = _group_cases(cases, "reaction_center_signature")
        self.by_template_pool = _group_cases(cases, "reaction_center_template_pool")
        if not self.hierarchy:
            self.hierarchy = infer_hierarchy(cases)

    def __len__(self) -> int:
        return len(self.cases)

    def get(self, case_id: str) -> Case | None:
        """Return a case by id, or ``None`` when absent."""
        return self.by_case_id.get(case_id)

    def require(self, case_id: str) -> Case:
        """Return a case by id, raising ``KeyError`` when absent."""
        return self.by_case_id[case_id]

    def cases_for_code(self, code: str) -> Tuple[Case, ...]:
        """Return cases attached to a Level 1-4 code."""
        if code in self.by_level4:
            return self.by_level4[code]
        if code in self.by_level3:
            return self.by_level3[code]
        if code in self.by_level2:
            return self.by_level2[code]
        if code in self.by_level1:
            return self.by_level1[code]
        return ()

    def children(self, code: str) -> Tuple[HierarchyNode, ...]:
        """Return direct child hierarchy nodes for a code."""
        children = [
            node for node in self.hierarchy.values() if node.parent_code == code
        ]
        return tuple(sorted(children, key=lambda node: node.code))

    def label_for_code(self, code: str) -> str | None:
        """Return the hierarchy label for a code, if known."""
        node = self.hierarchy.get(code)
        return node.name if node else None

    def level_counts(self, level: int) -> Dict[str, int]:
        """Return ``{code: case_count}`` for one hierarchy level."""
        return {
            code: node.case_count
            for code, node in self.hierarchy.items()
            if node.level == level
        }

    def case_count_by_level1(self) -> Dict[str, int]:
        """Return case counts by Level-1 regime."""
        return {code: len(cases) for code, cases in self.by_level1.items()}

    def level4_variant_counts(self) -> Dict[str, int]:
        """Return number of cases under each Level-4 label."""
        return {code: len(cases) for code, cases in self.by_level4.items()}

    def duplicate_case_ids(self) -> List[str]:
        """Return duplicate case ids, if any were loaded."""
        counts = Counter(case.case_id for case in self.cases)
        return sorted(case_id for case_id, count in counts.items() if count > 1)


def _group_cases(cases: Sequence[Case], attr: str) -> Dict[str, Tuple[Case, ...]]:
    grouped: Dict[str, List[Case]] = defaultdict(list)
    for case in cases:
        grouped[str(getattr(case, attr))].append(case)
    return {key: tuple(value) for key, value in grouped.items()}


def infer_hierarchy(cases: Iterable[Case]) -> Dict[str, HierarchyNode]:
    """Infer Level 1-4 hierarchy nodes from cases."""
    grouped: Dict[str, Dict[str, object]] = {}

    def add(code: str, name: str, level: int, parent: str | None) -> None:
        item = grouped.setdefault(
            code,
            {"name": name, "level": level, "parent": parent, "case_count": 0},
        )
        item["case_count"] = int(item["case_count"]) + 1

    for case in cases:
        add(case.level1_code, case.level1_name, 1, None)
        add(case.level2_code, case.level2_name, 2, case.level1_code)
        add(case.level3_code, case.level3_name, 3, case.level2_code)
        add(case.level4_code, case.level4_label, 4, case.level3_code)

    return {
        code: HierarchyNode(
            code=code,
            name=str(data["name"]),
            level=int(data["level"]),
            parent_code=(
                data["parent"] if data["parent"] is None else str(data["parent"])
            ),
            case_count=int(data["case_count"]),
        )
        for code, data in grouped.items()
    }


# Backward-compatible public name; new code should use ``CaseIndex``.
SynEPDDatabase = CaseIndex
