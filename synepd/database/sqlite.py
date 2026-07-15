"""SQLite-backed database for SynEPD case records."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, Mapping, Sequence, Tuple

from synepd.database.core import HierarchyNode, infer_hierarchy
from synepd.models import Case

SCHEMA_VERSION = 1


class CaseSQLiteStore:
    """Persistent SQLite view of SynEPD cases."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row

    @classmethod
    def connect(cls, path: Path | str) -> "CaseSQLiteStore":
        """Open an existing SQLite SynEPD database."""
        return cls(path)

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self.connection.close()

    def __enter__(self) -> "CaseSQLiteStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __len__(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) AS count FROM cases").fetchone()
        return int(row["count"])

    @property
    def summary(self) -> Mapping[str, object]:
        """Return the stored dataset summary."""
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key = 'summary'"
        ).fetchone()
        if row is None:
            return {}
        return json.loads(row["value"])

    @property
    def cases(self) -> Tuple[Case, ...]:
        """Return all cases in case-id order."""
        return self.query_cases()

    @property
    def hierarchy(self) -> Mapping[str, HierarchyNode]:
        """Return hierarchy nodes keyed by code."""
        rows = self.connection.execute("""
            SELECT code, name, level, parent_code, case_count
            FROM hierarchy
            ORDER BY level, code
            """).fetchall()
        return {
            row["code"]: HierarchyNode(
                code=row["code"],
                name=row["name"],
                level=int(row["level"]),
                parent_code=row["parent_code"],
                case_count=int(row["case_count"]),
            )
            for row in rows
        }

    def get(self, case_id: str) -> Case | None:
        """Return a case by id, or ``None`` when absent."""
        rows = self.query_cases(case_id=case_id)
        return rows[0] if rows else None

    def require(self, case_id: str) -> Case:
        """Return a case by id, raising ``KeyError`` when absent."""
        case = self.get(case_id)
        if case is None:
            raise KeyError(case_id)
        return case

    def cases_for_code(self, code: str) -> Tuple[Case, ...]:
        """Return cases attached to a Level 1-4 code."""
        return self.query_cases(code=code)

    def children(self, code: str) -> Tuple[HierarchyNode, ...]:
        """Return direct child hierarchy nodes for a code."""
        rows = self.connection.execute(
            """
            SELECT code, name, level, parent_code, case_count
            FROM hierarchy
            WHERE parent_code = ?
            ORDER BY code
            """,
            (code,),
        ).fetchall()
        return tuple(_node_from_row(row) for row in rows)

    def label_for_code(self, code: str) -> str | None:
        """Return the hierarchy label for a code, if known."""
        row = self.connection.execute(
            "SELECT name FROM hierarchy WHERE code = ?",
            (code,),
        ).fetchone()
        return None if row is None else str(row["name"])

    def level_counts(self, level: int) -> Dict[str, int]:
        """Return ``{code: case_count}`` for one hierarchy level."""
        rows = self.connection.execute(
            "SELECT code, case_count FROM hierarchy WHERE level = ? ORDER BY code",
            (level,),
        ).fetchall()
        return {str(row["code"]): int(row["case_count"]) for row in rows}

    def case_count_by_level1(self) -> Dict[str, int]:
        """Return case counts by Level-1 regime."""
        return self.level_counts(1)

    def level4_variant_counts(self) -> Dict[str, int]:
        """Return number of cases under each Level-4 label."""
        return self.level_counts(4)

    def duplicate_case_ids(self) -> list[str]:
        """Return duplicate case ids, if any are present."""
        rows = self.connection.execute("""
            SELECT case_id
            FROM cases
            GROUP BY case_id
            HAVING COUNT(*) > 1
            ORDER BY case_id
            """).fetchall()
        return [str(row["case_id"]) for row in rows]

    def search_labels(self, text: str) -> Tuple[HierarchyNode, ...]:
        """Search hierarchy labels by case-insensitive text."""
        needle = f"%{text.casefold()}%"
        rows = self.connection.execute(
            """
            SELECT code, name, level, parent_code, case_count
            FROM hierarchy
            WHERE lower(code) LIKE ? OR lower(name) LIKE ?
            ORDER BY level, code
            """,
            (needle, needle),
        ).fetchall()
        return tuple(_node_from_row(row) for row in rows)

    def query_cases(
        self,
        *,
        case_id: str | None = None,
        code: str | None = None,
        level1: str | None = None,
        level2: str | None = None,
        level3: str | None = None,
        level4: str | None = None,
        template_pool: str | None = None,
        signature: str | None = None,
        text: str | None = None,
    ) -> Tuple[Case, ...]:
        """Filter cases using indexed SQLite columns."""
        clauses: list[str] = []
        params: list[object] = []

        if case_id is not None:
            clauses.append("case_id = ?")
            params.append(case_id)
        if code is not None:
            clauses.append("""
                (
                    level1_code = ?
                    OR level2_code = ?
                    OR level3_code = ?
                    OR level4_code = ?
                )
                """)
            params.extend([code, code, code, code])
        for column, value in (
            ("level1_code", level1),
            ("level2_code", level2),
            ("level3_code", level3),
            ("level4_code", level4),
            ("reaction_center_template_pool", template_pool),
            ("reaction_center_signature", signature),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        if text is not None:
            clauses.append("""
                (
                    lower(level4_label) LIKE ?
                    OR lower(reaction_smiles) LIKE ?
                )
                """)
            needle = f"%{text.casefold()}%"
            params.extend([needle, needle])

        sql = "SELECT raw_json FROM cases"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY case_id"
        rows = self.connection.execute(sql, params).fetchall()
        return tuple(_case_from_row(row) for row in rows)


def write_sqlite_database(
    path: Path | str,
    cases: Iterable[Case],
    summary: Mapping[str, object] | None = None,
    *,
    overwrite: bool = True,
) -> CaseSQLiteStore:
    """Write cases to SQLite and return an opened database connection."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(path)

    case_tuple = tuple(cases)
    summary = summary or {}
    hierarchy = infer_hierarchy(case_tuple)

    connection = sqlite3.connect(path)
    try:
        _initialize_schema(connection)
        _insert_metadata(connection, summary)
        _insert_hierarchy(connection, hierarchy.values())
        _insert_cases(connection, case_tuple)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return CaseSQLiteStore(path)


def _initialize_schema(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        PRAGMA foreign_keys = ON;

        DROP TABLE IF EXISTS cases;
        DROP TABLE IF EXISTS hierarchy;
        DROP TABLE IF EXISTS metadata;

        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE hierarchy (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            level INTEGER NOT NULL,
            parent_code TEXT,
            case_count INTEGER NOT NULL
        );

        CREATE TABLE cases (
            case_id TEXT PRIMARY KEY,
            dataset_name TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            level1_code TEXT NOT NULL,
            level1_name TEXT NOT NULL,
            level2_code TEXT NOT NULL,
            level2_name TEXT NOT NULL,
            level3_code TEXT NOT NULL,
            level3_name TEXT NOT NULL,
            level4_code TEXT NOT NULL,
            level4_label TEXT NOT NULL,
            case_variant INTEGER NOT NULL,
            reaction_smiles TEXT NOT NULL,
            reaction_center_signature TEXT NOT NULL,
            reaction_center_template_pool TEXT NOT NULL,
            reaction_center_uniqueness_scope TEXT NOT NULL,
            shares_reaction_center_within_level4 INTEGER NOT NULL,
            atom_mapping_json TEXT NOT NULL,
            validation_status TEXT NOT NULL,
            curation_status TEXT NOT NULL,
            manual_review_required INTEGER NOT NULL,
            notes TEXT NOT NULL,
            raw_json TEXT NOT NULL
        );

        CREATE INDEX idx_cases_level1 ON cases(level1_code);
        CREATE INDEX idx_cases_level2 ON cases(level2_code);
        CREATE INDEX idx_cases_level3 ON cases(level3_code);
        CREATE INDEX idx_cases_level4 ON cases(level4_code);
        CREATE INDEX idx_cases_signature ON cases(reaction_center_signature);
        CREATE INDEX idx_cases_template_pool
            ON cases(reaction_center_template_pool);
        """)


def _insert_metadata(
    connection: sqlite3.Connection,
    summary: Mapping[str, object],
) -> None:
    rows = (
        ("schema_version", json.dumps(SCHEMA_VERSION)),
        ("summary", json.dumps(summary, sort_keys=True)),
    )
    connection.executemany(
        "INSERT INTO metadata(key, value) VALUES (?, ?)",
        rows,
    )


def _insert_hierarchy(
    connection: sqlite3.Connection,
    hierarchy: Iterable[HierarchyNode],
) -> None:
    rows = [
        (
            node.code,
            node.name,
            node.level,
            node.parent_code,
            node.case_count,
        )
        for node in hierarchy
    ]
    connection.executemany(
        """
        INSERT INTO hierarchy(code, name, level, parent_code, case_count)
        VALUES (?, ?, ?, ?, ?)
        """,
        rows,
    )


def _insert_cases(connection: sqlite3.Connection, cases: Sequence[Case]) -> None:
    rows = [_case_to_row(case) for case in cases]
    connection.executemany(
        """
        INSERT INTO cases(
            case_id,
            dataset_name,
            schema_version,
            level1_code,
            level1_name,
            level2_code,
            level2_name,
            level3_code,
            level3_name,
            level4_code,
            level4_label,
            case_variant,
            reaction_smiles,
            reaction_center_signature,
            reaction_center_template_pool,
            reaction_center_uniqueness_scope,
            shares_reaction_center_within_level4,
            atom_mapping_json,
            validation_status,
            curation_status,
            manual_review_required,
            notes,
            raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def _case_to_row(case: Case) -> tuple[object, ...]:
    raw = _case_to_dict(case)
    atom_mapping = raw.get("atom_mapping", {})
    return (
        case.case_id,
        case.dataset_name,
        case.schema_version,
        case.level1_code,
        case.level1_name,
        case.level2_code,
        case.level2_name,
        case.level3_code,
        case.level3_name,
        case.level4_code,
        case.level4_label,
        case.case_variant,
        case.reaction_smiles,
        case.reaction_center_signature,
        case.reaction_center_template_pool,
        case.reaction_center_uniqueness_scope,
        int(case.shares_reaction_center_within_level4),
        json.dumps(atom_mapping, sort_keys=True),
        case.validation_status,
        case.curation_status,
        int(case.manual_review_required),
        case.notes,
        json.dumps(raw, sort_keys=True),
    )


def _case_to_dict(case: Case) -> dict[str, object]:
    if case.raw:
        return dict(case.raw)
    return {
        "case_id": case.case_id,
        "dataset_name": case.dataset_name,
        "schema_version": case.schema_version,
        "level1_code": case.level1_code,
        "level1_name": case.level1_name,
        "level2_code": case.level2_code,
        "level2_name": case.level2_name,
        "level3_code": case.level3_code,
        "level3_name": case.level3_name,
        "level4_code": case.level4_code,
        "level4_label": case.level4_label,
        "case_variant": case.case_variant,
        "reaction_smiles": case.reaction_smiles,
        "reaction_center_signature": case.reaction_center_signature,
        "reaction_center_template_pool": case.reaction_center_template_pool,
        "reaction_center_uniqueness_scope": case.reaction_center_uniqueness_scope,
        "shares_reaction_center_within_level4": (
            case.shares_reaction_center_within_level4
        ),
        "atom_mapping": {
            "mapped_reaction_center": case.atom_mapping.mapped_reaction_center,
            "map_consistency_checked": case.atom_mapping.map_consistency_checked,
            "reactant_product_atom_map_sets_match": (
                case.atom_mapping.reactant_product_atom_map_sets_match
            ),
            "explicit_hydrogen_in_reaction_center": (
                case.atom_mapping.explicit_hydrogen_in_reaction_center
            ),
            "unmapped_explicit_hydrogen_present": (
                case.atom_mapping.unmapped_explicit_hydrogen_present
            ),
            "mapped_atom_count": case.atom_mapping.mapped_atom_count,
        },
        "validation_status": case.validation_status,
        "curation_status": case.curation_status,
        "manual_review_required": case.manual_review_required,
        "notes": case.notes,
    }


def _case_from_row(row: sqlite3.Row) -> Case:
    return Case.from_dict(json.loads(row["raw_json"]))


def _node_from_row(row: sqlite3.Row) -> HierarchyNode:
    return HierarchyNode(
        code=row["code"],
        name=row["name"],
        level=int(row["level"]),
        parent_code=row["parent_code"],
        case_count=int(row["case_count"]),
    )


# Backward-compatible public name; new code should use ``CaseSQLiteStore``.
SQLiteSynEPDDatabase = CaseSQLiteStore
