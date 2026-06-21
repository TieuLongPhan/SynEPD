"""SQLite database schema and models matching the UML chemical reaction schema."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class Molecule:
    id: Optional[int] = None
    structures: List[MoleculeStructure] = field(default_factory=list)
    substance_structures: List[SubstanceStructure] = field(default_factory=list)


@dataclass
class MoleculeStructure:
    id: Optional[int] = None
    molecule_id: Optional[int] = None
    signature: List[int] = field(default_factory=list)
    fingerprint: List[int] = field(default_factory=list)
    structure: bytes = b""


@dataclass
class NonOrganic:
    id: Optional[int] = None
    structure: bytes = b""
    substance_structures: List[SubstanceStructure] = field(default_factory=list)


@dataclass
class Substance:
    id: Optional[int] = None
    reactions: List[ReactionSubstance] = field(default_factory=list)
    components: List[SubstanceStructure] = field(default_factory=list)


@dataclass
class SubstanceStructure:
    id: Optional[int] = None
    substance_id: Optional[int] = None
    molar_fraction: float = 1.0
    molecule_id: Optional[int] = None
    non_organic_id: Optional[int] = None
    mapping: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReactionSubstance:
    id: Optional[int] = None
    reaction_id: Optional[int] = None
    substance_id: Optional[int] = None
    mapping: Dict[str, Any] = field(default_factory=dict)
    is_product: bool = False


@dataclass
class Reaction:
    id: Optional[int] = None
    substances: List[ReactionSubstance] = field(default_factory=list)
    cgr: Optional[CGR] = None


@dataclass
class CGR:
    id: Optional[int] = None
    reaction_id: Optional[int] = None
    fingerprint: List[int] = field(default_factory=list)
    structure: bytes = b""


class ChemistryDatabase:
    """Manager for the chemical reaction and substance relational database."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON;")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> ChemistryDatabase:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def create_tables(self) -> None:
        """Initialize all schema tables and indexes based on the UML definition."""
        with self.connection:
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS Molecule (
                    id INTEGER PRIMARY KEY AUTOINCREMENT
                );

                CREATE TABLE IF NOT EXISTS MoleculeStructure (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    molecule_id INTEGER NOT NULL,
                    signature TEXT,
                    fingerprint TEXT,
                    structure BLOB,
                    FOREIGN KEY (molecule_id) REFERENCES Molecule(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS NonOrganic (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    structure BLOB
                );

                CREATE TABLE IF NOT EXISTS Substance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT
                );

                CREATE TABLE IF NOT EXISTS SubstanceStructure (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    substance_id INTEGER NOT NULL,
                    molecule_id INTEGER,
                    non_organic_id INTEGER,
                    molar_fraction REAL NOT NULL,
                    mapping TEXT,
                    FOREIGN KEY (substance_id) REFERENCES Substance(id) ON DELETE CASCADE,
                    FOREIGN KEY (molecule_id) REFERENCES Molecule(id) ON DELETE SET NULL,
                    FOREIGN KEY (non_organic_id) REFERENCES NonOrganic(id) ON DELETE SET NULL,
                    CHECK (molecule_id IS NOT NULL OR non_organic_id IS NOT NULL)
                );

                CREATE TABLE IF NOT EXISTS Reaction (
                    id INTEGER PRIMARY KEY AUTOINCREMENT
                );

                CREATE TABLE IF NOT EXISTS ReactionSubstance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reaction_id INTEGER NOT NULL,
                    substance_id INTEGER NOT NULL,
                    mapping TEXT,
                    is_product INTEGER NOT NULL CHECK (is_product IN (0, 1)),
                    FOREIGN KEY (reaction_id) REFERENCES Reaction(id) ON DELETE CASCADE,
                    FOREIGN KEY (substance_id) REFERENCES Substance(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS CGR (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reaction_id INTEGER NOT NULL UNIQUE,
                    fingerprint TEXT,
                    structure BLOB,
                    FOREIGN KEY (reaction_id) REFERENCES Reaction(id) ON DELETE CASCADE
                );

                -- Indexes for fast query performance
                CREATE INDEX IF NOT EXISTS idx_molstruct_molecule ON MoleculeStructure(molecule_id);
                CREATE INDEX IF NOT EXISTS idx_substruct_substance ON SubstanceStructure(substance_id);
                CREATE INDEX IF NOT EXISTS idx_reactsub_reaction ON ReactionSubstance(reaction_id);
                CREATE INDEX IF NOT EXISTS idx_cgr_reaction ON CGR(reaction_id);
            """)

    def insert_molecule(self) -> int:
        """Insert a base Molecule record and return its ID."""
        with self.connection:
            cursor = self.connection.execute("INSERT INTO Molecule DEFAULT VALUES")
            return cursor.lastrowid

    def insert_molecule_structure(
        self,
        molecule_id: int,
        signature: List[int],
        fingerprint: List[int],
        structure: bytes,
    ) -> int:
        """Insert a MoleculeStructure linked to a Molecule."""
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO MoleculeStructure (molecule_id, signature, fingerprint, structure)
                VALUES (?, ?, ?, ?)
                """,
                (
                    molecule_id,
                    json.dumps(signature),
                    json.dumps(fingerprint),
                    sqlite3.Binary(structure),
                ),
            )
            return cursor.lastrowid

    def insert_non_organic(self, structure: bytes) -> int:
        """Insert a NonOrganic substance component structure and return its ID."""
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO NonOrganic (structure) VALUES (?)",
                (sqlite3.Binary(structure),),
            )
            return cursor.lastrowid

    def insert_substance(self) -> int:
        """Insert a Substance and return its ID."""
        with self.connection:
            cursor = self.connection.execute("INSERT INTO Substance DEFAULT VALUES")
            return cursor.lastrowid

    def insert_substance_structure(
        self,
        substance_id: int,
        molar_fraction: float,
        molecule_id: Optional[int] = None,
        non_organic_id: Optional[int] = None,
        mapping: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Insert a SubstanceStructure component (either organic Molecule or NonOrganic)."""
        mapping_data = json.dumps(mapping) if mapping is not None else "{}"
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO SubstanceStructure (substance_id, molar_fraction, molecule_id, non_organic_id, mapping)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    substance_id,
                    molar_fraction,
                    molecule_id,
                    non_organic_id,
                    mapping_data,
                ),
            )
            return cursor.lastrowid

    def insert_reaction(self) -> int:
        """Insert a Reaction and return its ID."""
        with self.connection:
            cursor = self.connection.execute("INSERT INTO Reaction DEFAULT VALUES")
            return cursor.lastrowid

    def insert_reaction_substance(
        self,
        reaction_id: int,
        substance_id: int,
        mapping: Dict[str, Any],
        is_product: bool,
    ) -> int:
        """Link a Substance to a Reaction as reactant or product."""
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO ReactionSubstance (reaction_id, substance_id, mapping, is_product)
                VALUES (?, ?, ?, ?)
                """,
                (
                    reaction_id,
                    substance_id,
                    json.dumps(mapping),
                    1 if is_product else 0,
                ),
            )
            return cursor.lastrowid

    def insert_cgr(
        self, reaction_id: int, fingerprint: List[int], structure: bytes
    ) -> int:
        """Insert the Condensed Graph of Reaction (CGR) mapping for a Reaction."""
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO CGR (reaction_id, fingerprint, structure)
                VALUES (?, ?, ?)
                """,
                (reaction_id, json.dumps(fingerprint), sqlite3.Binary(structure)),
            )
            return cursor.lastrowid
