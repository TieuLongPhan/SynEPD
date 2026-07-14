from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class DatasetRelease:
    version: str
    release_date: str
    license: str


DEFAULT_RELEASE = DatasetRelease(
    version="v0.1.0",
    release_date="2026-07-07",
    license="CC BY 4.0",
)


@dataclass
class Reaction:
    case_id: str
    canonical_rsmi: str
    aam_key: str
    name: Optional[str] = None
    id: Optional[int] = None
    components: List[ReactionComponent] = field(default_factory=list)
    taxonomy: Optional[ReactionTaxonomy] = None
    its: Optional[ITS] = None
    epd: Optional[EPD] = None
    balanced: Optional[int] = None
    reactant_atom_count: Optional[int] = None
    product_atom_count: Optional[int] = None
    formal_charge_delta: Optional[int] = None
    coords_json: Optional[str] = None


@dataclass
class Molecule:
    canonical_smiles: str
    inchikey: Optional[str] = None
    id: Optional[int] = None
    formula: Optional[str] = None
    exact_mass: Optional[float] = None
    num_heavy_atoms: Optional[int] = None
    num_rings: Optional[int] = None
    num_aromatic_rings: Optional[int] = None
    has_charge: Optional[int] = None
    morgan_fp: Optional[bytes] = None
    pattern_fp: Optional[bytes] = None
    iupac_name: Optional[str] = None
    cas_number: Optional[str] = None


@dataclass
class ReactionComponent:
    reaction_id: int
    molecule_id: int
    side: str
    component_index: int
    id: Optional[int] = None


@dataclass
class Taxon:
    code: str
    level: int
    name: str
    parent_code: Optional[str] = None


@dataclass
class ReactionTaxonomy:
    reaction_id: int
    taxon_code: str


@dataclass
class ReactionCenter:
    wlhash: str
    template_graph: bytes
    graph_format: str
    smarts: Optional[str] = None
    id: Optional[int] = None


@dataclass
class ITS:
    reaction_id: int
    rc_id: int
    wlhash: str
    graph_data: bytes
    graph_format: str


@dataclass
class EPD:
    reaction_id: int
    number_arrows: int
    signature: Optional[str] = None
    representation_mode: str = "exact"
    representation_json: Optional[str] = None
    arrows: List[EPDArrow] = field(default_factory=list)


@dataclass
class EPDArrowType:
    code: str
    source_type: str
    target_type: str
    electron_count: int
    arrow_style: str


@dataclass
class EPDArrow:
    reaction_id: int
    arrow_index: int
    arrow_type_code: str
    source_atoms: str
    target_atoms: str
    id: Optional[int] = None


class SynEPDDatabase:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON;")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> SynEPDDatabase:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def create_tables(self) -> None:
        with self.connection:
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS dataset_release (
                    version TEXT PRIMARY KEY,
                    release_date DATE,
                    license TEXT
                );

                CREATE TABLE IF NOT EXISTS reaction (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT NOT NULL UNIQUE,
                    canonical_rsmi TEXT NOT NULL,
                    aam_key TEXT NOT NULL UNIQUE,
                    name TEXT,
                    balanced INTEGER,
                    reactant_atom_count INTEGER,
                    product_atom_count INTEGER,
                    formal_charge_delta INTEGER,
                    coords_json TEXT
                );

                CREATE TABLE IF NOT EXISTS molecule (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    canonical_smiles TEXT NOT NULL UNIQUE,
                    inchikey TEXT,
                    formula TEXT,
                    exact_mass REAL,
                    num_heavy_atoms INTEGER,
                    num_rings INTEGER,
                    num_aromatic_rings INTEGER,
                    has_charge INTEGER,
                    morgan_fp BLOB,
                    pattern_fp BLOB,
                    iupac_name TEXT,
                    cas_number TEXT
                );

                CREATE TABLE IF NOT EXISTS reaction_component (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reaction_id INTEGER NOT NULL,
                    molecule_id INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    component_index INTEGER NOT NULL,
                    FOREIGN KEY (reaction_id) REFERENCES reaction(id) ON DELETE CASCADE,
                    FOREIGN KEY (molecule_id) REFERENCES molecule(id),
                    CHECK (side IN ('reactant', 'product')),
                    CHECK (component_index >= 1),
                    UNIQUE (reaction_id, side, component_index)
                );

                CREATE TABLE IF NOT EXISTS taxon (
                    code TEXT PRIMARY KEY,
                    parent_code TEXT,
                    level INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    FOREIGN KEY (parent_code) REFERENCES taxon(code),
                    CHECK (level BETWEEN 1 AND 4)
                );

                CREATE TABLE IF NOT EXISTS reaction_taxonomy (
                    reaction_id INTEGER NOT NULL,
                    taxon_code TEXT NOT NULL,
                    FOREIGN KEY (reaction_id) REFERENCES reaction(id) ON DELETE CASCADE,
                    FOREIGN KEY (taxon_code) REFERENCES taxon(code),
                    PRIMARY KEY (reaction_id, taxon_code)
                );

                CREATE TABLE IF NOT EXISTS reaction_center (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    wlhash TEXT NOT NULL UNIQUE,
                    template_graph BLOB NOT NULL,
                    graph_format TEXT NOT NULL,
                    smarts TEXT
                );

                CREATE TABLE IF NOT EXISTS its (
                    reaction_id INTEGER PRIMARY KEY,
                    rc_id INTEGER NOT NULL,
                    wlhash TEXT NOT NULL,
                    graph_data BLOB NOT NULL,
                    graph_format TEXT NOT NULL,
                    FOREIGN KEY (reaction_id) REFERENCES reaction(id) ON DELETE CASCADE,
                    FOREIGN KEY (rc_id) REFERENCES reaction_center(id)
                );

                CREATE TABLE IF NOT EXISTS epd (
                    reaction_id INTEGER PRIMARY KEY,
                    number_arrows INTEGER NOT NULL,
                    signature TEXT,
                    representation_mode TEXT NOT NULL DEFAULT 'exact',
                    representation_json TEXT,
                    FOREIGN KEY (reaction_id) REFERENCES its(reaction_id) ON DELETE CASCADE,
                    CHECK (number_arrows >= 1)
                );

                CREATE TABLE IF NOT EXISTS epd_arrow_type (
                    code TEXT PRIMARY KEY,
                    source_type TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    electron_count INTEGER NOT NULL,
                    arrow_style TEXT NOT NULL,
                    CHECK (electron_count IN (1, 2))
                );

                CREATE TABLE IF NOT EXISTS epd_arrow (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reaction_id INTEGER NOT NULL,
                    arrow_index INTEGER NOT NULL,
                    arrow_type_code TEXT NOT NULL,
                    source_atoms TEXT NOT NULL,
                    target_atoms TEXT NOT NULL,
                    FOREIGN KEY (reaction_id) REFERENCES epd(reaction_id) ON DELETE CASCADE,
                    FOREIGN KEY (arrow_type_code) REFERENCES epd_arrow_type(code),
                    CHECK (arrow_index >= 1),
                    UNIQUE (reaction_id, arrow_index)
                );

                CREATE INDEX IF NOT EXISTS idx_reaction_case_id ON reaction(case_id);
                CREATE INDEX IF NOT EXISTS idx_reaction_aam_key ON reaction(aam_key);
                CREATE INDEX IF NOT EXISTS idx_reaction_component_reaction ON reaction_component(reaction_id);
                CREATE INDEX IF NOT EXISTS idx_reaction_component_molecule ON reaction_component(molecule_id);
                CREATE INDEX IF NOT EXISTS idx_reaction_taxonomy_code ON reaction_taxonomy(taxon_code);
                CREATE INDEX IF NOT EXISTS idx_its_rc_id ON its(rc_id);
                CREATE INDEX IF NOT EXISTS idx_epd_number_arrows ON epd(number_arrows);
                CREATE INDEX IF NOT EXISTS idx_epd_arrow_type ON epd_arrow(arrow_type_code);
                CREATE INDEX IF NOT EXISTS idx_epd_arrow_index_type ON epd_arrow(arrow_index, arrow_type_code);
                CREATE INDEX IF NOT EXISTS idx_epd_arrow_reaction ON epd_arrow(reaction_id);
                CREATE INDEX IF NOT EXISTS idx_molecule_inchikey ON molecule(inchikey);
                CREATE INDEX IF NOT EXISTS idx_epd_signature ON epd(signature);

                CREATE VIRTUAL TABLE IF NOT EXISTS reaction_fts USING fts5(
                    name,
                    case_id,
                    content='reaction',
                    content_rowid='id'
                );

                CREATE TRIGGER IF NOT EXISTS reaction_fts_insert
                    AFTER INSERT ON reaction BEGIN
                        INSERT INTO reaction_fts(rowid, name, case_id)
                        VALUES (new.id, new.name, new.case_id);
                    END;

                CREATE TRIGGER IF NOT EXISTS reaction_fts_delete
                    AFTER DELETE ON reaction BEGIN
                        INSERT INTO reaction_fts(reaction_fts, rowid, name, case_id)
                        VALUES ('delete', old.id, old.name, old.case_id);
                    END;

                CREATE TRIGGER IF NOT EXISTS reaction_fts_update
                    AFTER UPDATE ON reaction BEGIN
                        INSERT INTO reaction_fts(reaction_fts, rowid, name, case_id)
                        VALUES ('delete', old.id, old.name, old.case_id);
                        INSERT INTO reaction_fts(rowid, name, case_id)
                        VALUES (new.id, new.name, new.case_id);
                    END;
            """)

            self.connection.execute(
                """
                INSERT OR REPLACE INTO dataset_release (version, release_date, license)
                VALUES (?, ?, ?);
                """,
                (
                    DEFAULT_RELEASE.version,
                    DEFAULT_RELEASE.release_date,
                    DEFAULT_RELEASE.license,
                ),
            )

            # Populate FTS if empty and reaction table has rows
            self.connection.execute("""
                INSERT INTO reaction_fts(rowid, name, case_id)
                SELECT id, name, case_id FROM reaction
                WHERE NOT EXISTS (SELECT 1 FROM reaction_fts);
            """)

    def init_vocabulary(self) -> None:
        arrows = [
            ("LP-/Sigma+", "LP", "SIGMA", 2, "curved"),
            ("LP-/Pi+", "LP", "PI", 2, "curved"),
            ("Sigma-/LP+", "SIGMA", "LP", 2, "curved"),
            ("Sigma-/Pi+", "SIGMA", "PI", 2, "curved"),
            ("Pi-/LP+", "PI", "LP", 2, "curved"),
            ("Pi-/Sigma+", "PI", "SIGMA", 2, "curved"),
            ("Pi-/Pi+", "PI", "PI", 2, "curved"),
            ("Sigma-/Sigma+", "SIGMA", "SIGMA", 2, "curved"),
        ]

        with self.connection:
            for arrow in arrows:
                self.connection.execute(
                    "INSERT OR IGNORE INTO epd_arrow_type (code, source_type, target_type, electron_count, arrow_style) VALUES (?, ?, ?, ?, ?)",
                    arrow,
                )
