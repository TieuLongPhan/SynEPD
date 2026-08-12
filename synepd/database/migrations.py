"""Transactional release-database migrations.

The v0.1 to v0.2 migration is deliberately offline: it derives safe graph
payloads and EPD-aware centers only from data already present in the database.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3

from synkit.Graph.Mech import LWGEditor
from synkit.Graph.ITS.rc_extractor import RCExtractor

from synepd.core.graph_codec import GRAPH_FORMAT, decode_graph, encode_graph
from synepd.core.ingest import extract_graphs
from synepd.core.mechanism import (
    MECHANISM_CONTEXT_VERSION,
    build_mechanistic_center_template_from_diagnostics,
    build_mechanistic_center_from_graphs,
    mechanistic_center_edge_counts,
    mechanistic_center_wlhash,
    mechanistic_centers_are_isomorphic,
    serialize_mechanism_context,
)
from synepd.core.representation import (
    remap_epd,
    remap_representation,
    representation_verification_rsmi,
)
from synepd.database.models import (
    CORE_RELEASE_SCHEMA_CHECKSUM,
    CORE_RELEASE_SCHEMA_VERSION,
    CURRENT_SCHEMA_VERSION,
    ITS_MECHANISTIC_CENTER_SCHEMA_CHECKSUM,
    ITS_MECHANISTIC_CENTER_SCHEMA_VERSION,
    MECHANISTIC_CENTER_SCHEMA_CHECKSUM,
    MECHANISTIC_CENTER_SCHEMA_VERSION,
    MECHANISM_CONTEXT_SCHEMA_CHECKSUM,
    MECHANISM_CONTEXT_SCHEMA_VERSION,
    REACTION_METADATA_SCHEMA_CHECKSUM,
    REACTION_METADATA_SCHEMA_VERSION,
    TAXON_XREF_SCHEMA_CHECKSUM,
    TAXON_XREF_SCHEMA_VERSION,
)

ONTOLOGY_RELEASE_ID = "rxno-2021-12-16"
ONTOLOGY_IRI = "http://purl.obolibrary.org/obo/rxno.owl"
ONTOLOGY_VERSION_IRI = (
    "http://purl.obolibrary.org/obo/rxno/releases/2021-12-16/rxno.owl"
)
EXPECTED_OBO_DATA_VERSION = "releases/2021-12-16"
EXPECTED_OBO_SHA256 = "cf501cf34c8c9c2c3003033dc2e0eea366ed9cb4a0c4772387c1d4b1477e1a92"
ONTOLOGY_LICENSE_IRI = "http://creativecommons.org/licenses/by/4.0/"
ALLOWED_MAPPING_RELATIONS = {
    "skos:exactMatch",
    "skos:broadMatch",
    "skos:closeMatch",
    "dcterms:isPartOf",
}
CROSSWALK_HEADER = (
    "taxon_code",
    "taxon_level",
    "taxon_name",
    "relation",
    "match_type",
    "score",
    "ontology_id",
    "ontology_name",
)


@dataclass(frozen=True)
class MigrationReport:
    schema_version: str
    reaction_count: int
    context_count: int
    representation_count: int
    epd_repair_count: int
    reaction_center_graph_count: int
    its_graph_count: int
    xref_count: int = 0
    ontology_release_count: int = 0
    mechanistic_center_count: int = 0
    its_mechanistic_center_count: int = 0
    already_applied: bool = False


class MigrationError(RuntimeError):
    """Raised when an atomic database migration cannot be completed."""


def migrate_release_database(
    path: Path | str,
    *,
    source_path: Path | str | None = None,
    crosswalk_path: Path | str | None = None,
    obo_path: Path | str | None = None,
) -> MigrationReport:
    """Apply ordered mechanism-context, ontology-link, and metadata migrations."""
    database_path = Path(path)
    source_records = _load_source_records(source_path)
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        connection.execute("BEGIN IMMEDIATE")
        _ensure_mechanism_context_tables(connection)
        changed = False
        mechanism_report = None

        if not _migration_is_applied(
            connection,
            MECHANISM_CONTEXT_SCHEMA_VERSION,
            MECHANISM_CONTEXT_SCHEMA_CHECKSUM,
        ):
            mechanism_report = _apply_mechanism_context_migration(
                connection, source_records
            )
            _record_migration(
                connection,
                MECHANISM_CONTEXT_SCHEMA_VERSION,
                MECHANISM_CONTEXT_SCHEMA_CHECKSUM,
            )
            changed = True

        if not _migration_is_applied(
            connection, TAXON_XREF_SCHEMA_VERSION, TAXON_XREF_SCHEMA_CHECKSUM
        ):
            resolved_crosswalk = (
                Path(crosswalk_path)
                if crosswalk_path is not None
                else database_path.parent / "rxno_crosswalk.tsv"
            )
            resolved_obo = (
                Path(obo_path)
                if obo_path is not None
                else database_path.parent / "rxno.obo"
            )
            _apply_taxon_xref_migration(
                connection,
                crosswalk_path=resolved_crosswalk,
                obo_path=resolved_obo,
            )
            _record_migration(
                connection, TAXON_XREF_SCHEMA_VERSION, TAXON_XREF_SCHEMA_CHECKSUM
            )
            changed = True

        if not _migration_is_applied(
            connection,
            REACTION_METADATA_SCHEMA_VERSION,
            REACTION_METADATA_SCHEMA_CHECKSUM,
        ):
            reaction_count = connection.execute(
                "SELECT COUNT(*) FROM reaction"
            ).fetchone()[0]
            if reaction_count:
                if source_path is None:
                    raise MigrationError(
                        "Schema 004 requires source JSON to backfill a nonempty "
                        "release database"
                    )
                database_case_ids = {
                    str(row[0])
                    for row in connection.execute("SELECT case_id FROM reaction")
                }
                source_case_ids = set(source_records)
                missing_source = sorted(database_case_ids - source_case_ids)
                unexpected_source = sorted(source_case_ids - database_case_ids)
                if missing_source or unexpected_source:
                    raise MigrationError(
                        "Schema 004 source coverage mismatch: "
                        f"missing={missing_source}, unexpected={unexpected_source}"
                    )
            _ensure_release_metadata_tables(connection)
            _populate_reaction_metadata(connection, source_records)
            _record_migration(
                connection,
                REACTION_METADATA_SCHEMA_VERSION,
                REACTION_METADATA_SCHEMA_CHECKSUM,
            )
            changed = True

        if not _migration_is_applied(
            connection,
            MECHANISTIC_CENTER_SCHEMA_VERSION,
            MECHANISTIC_CENTER_SCHEMA_CHECKSUM,
        ):
            _ensure_mechanistic_center_tables(connection)
            _apply_mechanistic_center_migration(connection)
            _record_migration(
                connection,
                MECHANISTIC_CENTER_SCHEMA_VERSION,
                MECHANISTIC_CENTER_SCHEMA_CHECKSUM,
            )
            changed = True

        if not _migration_is_applied(
            connection,
            ITS_MECHANISTIC_CENTER_SCHEMA_VERSION,
            ITS_MECHANISTIC_CENTER_SCHEMA_CHECKSUM,
        ):
            _apply_its_mechanistic_center_migration(connection)
            _record_migration(
                connection,
                ITS_MECHANISTIC_CENTER_SCHEMA_VERSION,
                ITS_MECHANISTIC_CENTER_SCHEMA_CHECKSUM,
            )
            changed = True

        if not _migration_is_applied(
            connection,
            CORE_RELEASE_SCHEMA_VERSION,
            CORE_RELEASE_SCHEMA_CHECKSUM,
        ):
            _apply_core_release_migration(connection)
            _record_migration(
                connection,
                CORE_RELEASE_SCHEMA_VERSION,
                CORE_RELEASE_SCHEMA_CHECKSUM,
            )
            changed = True

        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise MigrationError(
                f"Foreign-key validation failed with {len(violations)} violation(s)"
            )
        reaction_count = connection.execute("SELECT COUNT(*) FROM reaction").fetchone()[
            0
        ]
        context_count = connection.execute(
            "SELECT COUNT(*) FROM mechanism_context"
        ).fetchone()[0]
        xref_count = _table_count(connection, "taxon_xref")
        ontology_release_count = _table_count(connection, "ontology_release")
        mechanistic_center_count = connection.execute(
            "SELECT COUNT(*) FROM mechanistic_center"
        ).fetchone()[0]
        its_mechanistic_center_count = connection.execute(
            "SELECT COUNT(*) FROM its WHERE mc_id IS NOT NULL"
        ).fetchone()[0]
        connection.commit()
        return MigrationReport(
            schema_version=CURRENT_SCHEMA_VERSION,
            reaction_count=reaction_count,
            context_count=context_count,
            representation_count=(
                mechanism_report.representation_count if mechanism_report else 0
            ),
            epd_repair_count=(
                mechanism_report.epd_repair_count if mechanism_report else 0
            ),
            reaction_center_graph_count=(
                mechanism_report.reaction_center_graph_count if mechanism_report else 0
            ),
            its_graph_count=(
                mechanism_report.its_graph_count if mechanism_report else 0
            ),
            xref_count=xref_count,
            ontology_release_count=ontology_release_count,
            mechanistic_center_count=mechanistic_center_count,
            its_mechanistic_center_count=its_mechanistic_center_count,
            already_applied=not changed,
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _migration_is_applied(
    connection: sqlite3.Connection, version: str, checksum: str
) -> bool:
    row = connection.execute(
        "SELECT checksum FROM schema_migration WHERE version = ?", (version,)
    ).fetchone()
    if row is None:
        return False
    if row[0] != checksum:
        raise MigrationError(f"Migration checksum mismatch for {version}")
    return True


def _record_migration(
    connection: sqlite3.Connection, version: str, checksum: str
) -> None:
    connection.execute(
        """
        INSERT INTO schema_migration (version, applied_at, checksum)
        VALUES (?, datetime('now'), ?)
        """,
        (version, checksum),
    )


def _table_count(connection: sqlite3.Connection, table: str) -> int:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] if exists else 0


def _apply_core_release_migration(connection: sqlite3.Connection) -> None:
    """Remove catalog/linkage copies that are authoritative outside SQLite."""
    connection.execute("DROP TABLE IF EXISTS reaction_relation")
    connection.execute("DROP TABLE IF EXISTS reaction_alias")
    connection.execute("DROP TABLE IF EXISTS taxon_xref")
    connection.execute("DROP TABLE IF EXISTS ontology_release")


def _ensure_mechanism_context_tables(connection: sqlite3.Connection) -> None:
    reaction_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(reaction)")
    }
    if "canonical_aam_key" not in reaction_columns:
        connection.execute("ALTER TABLE reaction ADD COLUMN canonical_aam_key TEXT")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS schema_migration (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL,
            checksum TEXT NOT NULL
        )
        """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS mechanism_context (
            reaction_id INTEGER PRIMARY KEY,
            construction_version TEXT NOT NULL,
            context_hash TEXT NOT NULL,
            anchor_graph BLOB NOT NULL,
            graph_format TEXT NOT NULL,
            events_json TEXT NOT NULL,
            diagnostics_json TEXT NOT NULL,
            FOREIGN KEY (reaction_id) REFERENCES epd(reaction_id) ON DELETE CASCADE
        )
        """)
    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_mechanism_context_hash
        ON mechanism_context(context_hash)
        """)


def _ensure_mechanistic_center_tables(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS mechanistic_center (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rc_id INTEGER NOT NULL,
            wlhash TEXT NOT NULL UNIQUE,
            template_graph BLOB NOT NULL,
            graph_format TEXT NOT NULL,
            transition_edge_count INTEGER NOT NULL DEFAULT 0,
            rc_extension_edge_count INTEGER NOT NULL DEFAULT 0,
            transient_only_edge_count INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (rc_id) REFERENCES reaction_center(id),
            CHECK (transition_edge_count >= 0),
            CHECK (rc_extension_edge_count >= 0),
            CHECK (transient_only_edge_count >= 0),
            CHECK (rc_extension_edge_count <= transition_edge_count),
            CHECK (transient_only_edge_count <= transition_edge_count)
        )
        """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS reaction_mechanistic_center (
            reaction_id INTEGER PRIMARY KEY,
            mc_id INTEGER NOT NULL,
            FOREIGN KEY (reaction_id) REFERENCES epd(reaction_id) ON DELETE CASCADE,
            FOREIGN KEY (mc_id) REFERENCES mechanistic_center(id)
        )
        """)
    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_mechanistic_center_rc
        ON mechanistic_center(rc_id)
        """)
    connection.execute("""
        CREATE INDEX IF NOT EXISTS idx_reaction_mechanistic_center_mc
        ON reaction_mechanistic_center(mc_id)
        """)


def _apply_mechanistic_center_migration(connection: sqlite3.Connection) -> None:
    """Derive reusable induced-RC + EPD-transition templates offline."""
    connection.execute("DELETE FROM reaction_mechanistic_center")
    connection.execute("DELETE FROM mechanistic_center")
    cache: dict[int, dict[str, list[tuple[int, object]]]] = {}
    rows = connection.execute("""
        SELECT context.reaction_id, its.rc_id, context.anchor_graph,
               context.graph_format, context.diagnostics_json
        FROM mechanism_context AS context
        JOIN its ON its.reaction_id = context.reaction_id
        ORDER BY context.reaction_id
        """).fetchall()
    for reaction_id, rc_id, raw_graph, graph_format, diagnostics_json in rows:
        anchor_graph = decode_graph(raw_graph, graph_format, allow_legacy=True)
        diagnostics = json.loads(diagnostics_json)
        template = build_mechanistic_center_template_from_diagnostics(
            anchor_graph, diagnostics
        )
        raw_hash = mechanistic_center_wlhash(template)
        hash_cache = cache.setdefault(int(rc_id), {}).setdefault(raw_hash, [])
        mc_id = next(
            (
                candidate_id
                for candidate_id, candidate in hash_cache
                if mechanistic_centers_are_isomorphic(template, candidate)
            ),
            None,
        )
        if mc_id is None:
            edge_counts = mechanistic_center_edge_counts(template)
            stored_hash = raw_hash
            suffix = 1
            while connection.execute(
                "SELECT 1 FROM mechanistic_center WHERE wlhash = ?", (stored_hash,)
            ).fetchone():
                stored_hash = f"{raw_hash}_{suffix}"
                suffix += 1
            cursor = connection.execute(
                """
                INSERT INTO mechanistic_center (
                    rc_id, wlhash, template_graph, graph_format,
                    transition_edge_count, rc_extension_edge_count,
                    transient_only_edge_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rc_id,
                    stored_hash,
                    encode_graph(template),
                    GRAPH_FORMAT,
                    edge_counts.transition,
                    edge_counts.rc_extension,
                    edge_counts.transient_only,
                ),
            )
            mc_id = int(cursor.lastrowid)
            hash_cache.append((mc_id, template))
        connection.execute(
            """
            INSERT INTO reaction_mechanistic_center (reaction_id, mc_id)
            VALUES (?, ?)
            """,
            (reaction_id, mc_id),
        )

    context_count = connection.execute(
        "SELECT COUNT(*) FROM mechanism_context"
    ).fetchone()[0]
    association_count = connection.execute(
        "SELECT COUNT(*) FROM reaction_mechanistic_center"
    ).fetchone()[0]
    if association_count != context_count:
        raise MigrationError(
            "Mechanistic-center migration did not link every mechanism context: "
            f"contexts={context_count}, links={association_count}"
        )


def _apply_its_mechanistic_center_migration(
    connection: sqlite3.Connection,
) -> None:
    """Move each reaction's reusable MC assignment onto its ITS row."""
    its_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(its)").fetchall()
    }
    if "mc_id" not in its_columns:
        connection.execute(
            "ALTER TABLE its ADD COLUMN mc_id INTEGER "
            "REFERENCES mechanistic_center(id)"
        )

    association_table_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' "
        "AND name = 'reaction_mechanistic_center'"
    ).fetchone()
    if association_table_exists:
        connection.execute("""
            UPDATE its
            SET mc_id = (
                SELECT links.mc_id
                FROM reaction_mechanistic_center AS links
                WHERE links.reaction_id = its.reaction_id
            )
            WHERE EXISTS (
                SELECT 1
                FROM reaction_mechanistic_center AS links
                WHERE links.reaction_id = its.reaction_id
            )
            """)

    context_count = connection.execute(
        "SELECT COUNT(*) FROM mechanism_context"
    ).fetchone()[0]
    linked_count = connection.execute("""
        SELECT COUNT(*)
        FROM mechanism_context AS context
        JOIN its ON its.reaction_id = context.reaction_id
        WHERE its.mc_id IS NOT NULL
        """).fetchone()[0]
    if linked_count != context_count:
        raise MigrationError(
            "ITS mechanistic-center migration did not link every mechanism "
            f"context: contexts={context_count}, links={linked_count}"
        )

    inconsistent_count = connection.execute("""
        SELECT COUNT(*)
        FROM its
        JOIN mechanistic_center AS mc ON mc.id = its.mc_id
        WHERE its.mc_id IS NOT NULL AND mc.rc_id != its.rc_id
        """).fetchone()[0]
    if inconsistent_count:
        raise MigrationError(
            "ITS mechanistic-center assignments disagree with their RC: "
            f"count={inconsistent_count}"
        )

    connection.execute("DROP INDEX IF EXISTS idx_reaction_mechanistic_center_mc")
    connection.execute("DROP TABLE IF EXISTS reaction_mechanistic_center")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_its_mc_id ON its(mc_id)")


def _ensure_release_metadata_tables(connection: sqlite3.Connection) -> None:
    """Create stable reaction entry codes owned by schema 004."""
    statements = (
        """CREATE TABLE IF NOT EXISTS reaction_entry_code (
            reaction_id INTEGER NOT NULL,
            taxon_code TEXT NOT NULL,
            entry_code TEXT NOT NULL UNIQUE,
            code_index INTEGER NOT NULL,
            is_primary INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (reaction_id) REFERENCES reaction(id) ON DELETE CASCADE,
            FOREIGN KEY (taxon_code) REFERENCES taxon(code),
            CHECK (length(trim(entry_code)) > 0),
            CHECK (code_index >= 0),
            CHECK (is_primary IN (0, 1)),
            PRIMARY KEY (reaction_id, entry_code),
            UNIQUE (reaction_id, taxon_code),
            UNIQUE (reaction_id, code_index)
        )""",
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_reaction_entry_code_primary
        ON reaction_entry_code(reaction_id) WHERE is_primary = 1""",
    )
    for statement in statements:
        connection.execute(statement)


def _validate_pinned_obo(path: Path) -> set[str]:
    if not path.is_file():
        raise MigrationError(f"Pinned RXNO snapshot is missing: {path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != EXPECTED_OBO_SHA256:
        raise MigrationError(
            f"Pinned RXNO checksum mismatch: expected {EXPECTED_OBO_SHA256}, got {digest}"
        )
    version = "unknown"
    active_ids: set[str] = set()
    current_id: str | None = None
    obsolete = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("data-version:"):
            version = line.split(":", 1)[1].strip()
        elif line == "[Term]":
            if current_id and not obsolete:
                active_ids.add(current_id)
            current_id = None
            obsolete = False
        elif line.startswith("id: "):
            candidate = line[4:]
            current_id = candidate if candidate.startswith(("RXNO:", "MOP:")) else None
        elif line == "is_obsolete: true":
            obsolete = True
    if current_id and not obsolete:
        active_ids.add(current_id)
    if version != EXPECTED_OBO_DATA_VERSION:
        raise MigrationError(
            f"Pinned RXNO data-version mismatch: expected "
            f"{EXPECTED_OBO_DATA_VERSION}, got {version}"
        )
    return active_ids


def _read_crosswalk_rows(
    connection: sqlite3.Connection, path: Path, ontology_ids: set[str]
) -> list[tuple]:
    if not path.is_file():
        raise MigrationError(f"RXNO crosswalk is missing: {path}")
    active_taxa = {row[0] for row in connection.execute("SELECT code FROM taxon")}
    rows: list[tuple] = []
    seen: set[tuple[str, str]] = set()
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if tuple(reader.fieldnames or ()) != CROSSWALK_HEADER:
            raise MigrationError(f"{path} has an invalid crosswalk header")
        for line_no, row in enumerate(reader, start=2):
            code = row["taxon_code"]
            ontology_id = row["ontology_id"]
            relation = row["relation"]
            if code not in active_taxa:
                raise MigrationError(
                    f"{path}:{line_no} references inactive taxon {code}"
                )
            if ontology_id not in ontology_ids:
                raise MigrationError(
                    f"{path}:{line_no} references unknown ontology term {ontology_id}"
                )
            if relation not in ALLOWED_MAPPING_RELATIONS:
                raise MigrationError(
                    f"{path}:{line_no} has invalid relation {relation}"
                )
            key = (code, ontology_id)
            if key in seen:
                raise MigrationError(f"{path}:{line_no} duplicates {key}")
            seen.add(key)
            try:
                score = float(row["score"])
            except ValueError as exc:
                raise MigrationError(f"{path}:{line_no} has invalid score") from exc
            if not 0.0 <= score <= 1.0:
                raise MigrationError(f"{path}:{line_no} has out-of-range score")
            rows.append(
                (
                    code,
                    ontology_id,
                    relation,
                    row["match_type"],
                    score,
                    row["ontology_name"],
                    ONTOLOGY_RELEASE_ID,
                )
            )
    return rows


def _apply_taxon_xref_migration(
    connection: sqlite3.Connection,
    *,
    crosswalk_path: Path,
    obo_path: Path,
) -> None:
    ontology_ids = _validate_pinned_obo(obo_path)
    rows = _read_crosswalk_rows(connection, crosswalk_path, ontology_ids)
    connection.execute("DROP TABLE IF EXISTS taxon_xref")
    connection.execute("DROP TABLE IF EXISTS ontology_release")
    connection.execute("""
        CREATE TABLE ontology_release (
            id TEXT PRIMARY KEY,
            ontology_iri TEXT NOT NULL,
            version_iri TEXT NOT NULL,
            data_version TEXT NOT NULL,
            source_sha256 TEXT NOT NULL CHECK (length(source_sha256) = 64),
            license_iri TEXT NOT NULL
        )
        """)
    connection.execute("""
        CREATE TABLE taxon_xref (
            taxon_code TEXT NOT NULL,
            ontology_id TEXT NOT NULL,
            relation TEXT NOT NULL,
            match_type TEXT NOT NULL,
            score REAL NOT NULL,
            ontology_name TEXT NOT NULL,
            ontology_release_id TEXT NOT NULL,
            FOREIGN KEY (taxon_code) REFERENCES taxon(code) ON DELETE CASCADE,
            FOREIGN KEY (ontology_release_id) REFERENCES ontology_release(id),
            PRIMARY KEY (taxon_code, ontology_id),
            CHECK (relation IN (
                'skos:exactMatch', 'skos:broadMatch', 'skos:closeMatch',
                'dcterms:isPartOf'
            )),
            CHECK (score >= 0.0 AND score <= 1.0)
        )
        """)
    connection.execute(
        """
        INSERT INTO ontology_release (
            id, ontology_iri, version_iri, data_version, source_sha256,
            license_iri
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            ONTOLOGY_RELEASE_ID,
            ONTOLOGY_IRI,
            ONTOLOGY_VERSION_IRI,
            EXPECTED_OBO_DATA_VERSION,
            EXPECTED_OBO_SHA256,
            ONTOLOGY_LICENSE_IRI,
        ),
    )
    connection.executemany(
        """
        INSERT INTO taxon_xref (
            taxon_code, ontology_id, relation, match_type, score,
            ontology_name, ontology_release_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    connection.execute(
        "CREATE INDEX idx_taxon_xref_ontology ON taxon_xref(ontology_id)"
    )


def materialize_taxon_xrefs(
    connection: sqlite3.Connection,
    *,
    crosswalk_path: Path | str,
    obo_path: Path | str,
) -> None:
    """Populate pinned RXNO/MOP provenance and validated taxon links."""
    _apply_taxon_xref_migration(
        connection,
        crosswalk_path=Path(crosswalk_path),
        obo_path=Path(obo_path),
    )


def _populate_reaction_metadata(
    connection: sqlite3.Connection, source_records: dict[str, dict]
) -> None:
    """Populate stable reaction entry codes from the clean source."""
    connection.execute("DELETE FROM reaction_entry_code")
    if not source_records:
        return

    database_reactions = {
        str(case_id): int(reaction_id)
        for reaction_id, case_id in connection.execute(
            "SELECT id, case_id FROM reaction"
        )
    }
    for case_id, record in source_records.items():
        reaction_id = database_reactions.get(case_id)
        if reaction_id is None:
            continue
        codes = record.get("entry_codes") or (
            [record["entry_code"]] if record.get("entry_code") else []
        )
        taxon_codes = record.get("tax_codes") or (
            [record["tax_code"]] if record.get("tax_code") else []
        )
        if len(codes) != len(taxon_codes):
            raise MigrationError(
                f"Reaction {case_id} entry_codes and tax_codes have unequal length"
            )
        connection.executemany(
            """
            INSERT INTO reaction_entry_code (
                reaction_id, taxon_code, entry_code, code_index, is_primary
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                (
                    reaction_id,
                    taxon_codes[code_index],
                    code,
                    code_index,
                    int(code_index == 0),
                )
                for code_index, code in enumerate(codes)
            ),
        )


def _load_source_records(source_path: Path | str | None) -> dict[str, dict]:
    if source_path is None:
        return {}
    with Path(source_path).open(encoding="utf-8") as handle:
        payload = json.load(handle)
    records = payload.get("records", payload)
    return {
        record.get("case_id")
        or f"{record.get('family', 'polar')}_{int(record['id']):06d}": record
        for record in records
    }


def _apply_mechanism_context_migration(
    connection: sqlite3.Connection,
    source_records: dict[str, dict],
) -> MigrationReport:
    editor = LWGEditor()
    reaction_ids = [
        row[0]
        for row in connection.execute(
            "SELECT reaction_id FROM epd ORDER BY reaction_id"
        ).fetchall()
    ]
    representation_count = 0
    epd_repair_count = 0
    rc_cache: dict[int, object] = {}

    try:
        for reaction_id in reaction_ids:
            row = connection.execute(
                """
                SELECT r.case_id, r.canonical_rsmi, r.aam_key, i.rc_id,
                       i.graph_data, i.graph_format,
                       e.representation_mode, e.representation_json
                FROM reaction r
                JOIN its i ON i.reaction_id = r.id
                JOIN epd e ON e.reaction_id = r.id
                WHERE r.id = ?
                """,
                (reaction_id,),
            ).fetchone()
            if row is None:
                raise MigrationError(f"Reaction {reaction_id} has incomplete EPD data")
            (
                case_id,
                _canonical_rsmi,
                aam_key,
                rc_id,
                its_blob,
                its_format,
                representation_mode,
                representation_json,
            ) = row
            its_graph = decode_graph(its_blob, its_format, allow_legacy=True)

            if rc_id not in rc_cache:
                rc_blob, rc_format = connection.execute(
                    """
                    SELECT template_graph, graph_format
                    FROM reaction_center WHERE id = ?
                    """,
                    (rc_id,),
                ).fetchone()
                rc_cache[rc_id] = decode_graph(rc_blob, rc_format, allow_legacy=True)
            # A reaction_center row is shared across isomorphic reactions and
            # therefore carries the atom-map namespace of its first member.
            # Derive the per-reaction direct center from ITS before combining
            # it with this reaction's EPD context.
            direct_center = RCExtractor().extract(its_graph)

            arrow_rows = connection.execute(
                """
                SELECT arrow_type_code, source_atoms, target_atoms
                FROM epd_arrow WHERE reaction_id = ? ORDER BY arrow_index
                """,
                (reaction_id,),
            ).fetchall()
            epd = [
                [action, json.loads(source), json.loads(target)]
                for action, source, target in arrow_rows
            ]

            stored_representation = (
                json.loads(representation_json) if representation_json else None
            )
            source_record = source_records.get(case_id)
            if source_record is not None:
                base_epd = source_record.get("epd", [])
                base_representation = source_record.get("epd_representation")
                curated_aam_key = source_record["rsmi"]
                source_graphs = extract_graphs(curated_aam_key)
                if source_graphs is None:
                    raise MigrationError(
                        f"Reaction {reaction_id} source graphs could not be constructed"
                    )
                its_graph, direct_center, _ = source_graphs
                canonical_aam_key = aam_key
                aam_key = curated_aam_key
                translations = [{}]
            else:
                base_epd = epd
                base_representation = stored_representation
                translations = [
                    {
                        int(attributes.get("atom_map", node)): int(
                            attributes.get("atom_map", node)
                        )
                        for node, attributes in its_graph.nodes(data=True)
                    }
                ]

            selected_epd = None
            selected_representation = None
            edit_result = None
            for translation in translations:
                candidate_epd = remap_epd(base_epd, translation)
                candidate_representation = remap_representation(
                    base_representation,
                    translation,
                    namespace=(
                        "curated_aam_key"
                        if source_record is not None and base_representation
                        else "canonical_aam_key" if base_representation else None
                    ),
                )
                try:
                    verification_rsmi = representation_verification_rsmi(
                        aam_key, candidate_representation
                    )
                    candidate_result = editor.apply(verification_rsmi, candidate_epd)
                except Exception:
                    candidate_result = None
                if candidate_result is not None and candidate_result.matches_product:
                    selected_epd = candidate_epd
                    selected_representation = candidate_representation
                    edit_result = candidate_result
                    break
            if selected_epd is None or edit_result is None:
                raise MigrationError(
                    f"Reaction {reaction_id} has no product-verifying canonical EPD "
                    "mapping; provide the matching source JSON for a legacy artifact"
                )

            if selected_epd != epd:
                epd_repair_count += 1
                for arrow_index, (_, source, target) in enumerate(
                    selected_epd, start=1
                ):
                    connection.execute(
                        """
                        UPDATE epd_arrow SET source_atoms = ?, target_atoms = ?
                        WHERE reaction_id = ? AND arrow_index = ?
                        """,
                        (
                            json.dumps(source),
                            json.dumps(target),
                            reaction_id,
                            arrow_index,
                        ),
                    )
            epd = selected_epd
            representation = selected_representation

            if source_record is not None:
                connection.execute(
                    """
                    UPDATE reaction SET aam_key = ?, canonical_aam_key = ?
                    WHERE id = ?
                    """,
                    (aam_key, canonical_aam_key, reaction_id),
                )

            if source_record is not None and base_representation:
                representation_count += 1
            if representation:
                connection.execute(
                    """
                    UPDATE epd SET representation_json = ?, representation_mode = ?
                    WHERE reaction_id = ?
                    """,
                    (
                        json.dumps(representation, sort_keys=True),
                        str(representation.get("mode", representation_mode)),
                        reaction_id,
                    ),
                )

            if representation and representation.get("atom_map_namespace") not in {
                "canonical_aam_key",
                "curated_aam_key",
            }:
                raise MigrationError(
                    f"Reaction {reaction_id} has stale representation maps"
                )
            center = build_mechanistic_center_from_graphs(
                its_graph,
                direct_center,
                epd,
                step_reports=edit_result.step_reports,
            )
            context = serialize_mechanism_context(center)
            connection.execute(
                """
                INSERT OR REPLACE INTO mechanism_context (
                    reaction_id, construction_version, context_hash,
                    anchor_graph, graph_format, events_json, diagnostics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reaction_id,
                    MECHANISM_CONTEXT_VERSION,
                    context.context_hash,
                    context.anchor_graph,
                    GRAPH_FORMAT,
                    context.events_json,
                    context.diagnostics_json,
                ),
            )
            connection.execute(
                """
                UPDATE its SET graph_data = ?, graph_format = ?
                WHERE reaction_id = ?
                """,
                (encode_graph(its_graph), GRAPH_FORMAT, reaction_id),
            )

        for rc_id, rc_graph in rc_cache.items():
            connection.execute(
                """
                UPDATE reaction_center SET template_graph = ?, graph_format = ?
                WHERE id = ?
                """,
                (encode_graph(rc_graph), GRAPH_FORMAT, rc_id),
            )
    except Exception as exc:
        if isinstance(exc, MigrationError):
            raise
        raise MigrationError(
            f"Migration failed while processing reaction {reaction_id}: {exc}"
        ) from exc

    return MigrationReport(
        schema_version=MECHANISM_CONTEXT_SCHEMA_VERSION,
        reaction_count=len(reaction_ids),
        context_count=len(reaction_ids),
        representation_count=representation_count,
        epd_repair_count=epd_repair_count,
        reaction_center_graph_count=len(rc_cache),
        its_graph_count=len(reaction_ids),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument(
        "--source",
        type=Path,
        help="Source corpus JSON required to repair legacy atom-map information",
    )
    parser.add_argument(
        "--crosswalk",
        type=Path,
        help="Validated SynEPD-to-RXNO/MOP crosswalk required for schema 003",
    )
    parser.add_argument(
        "--obo",
        type=Path,
        help="Pinned RXNO OBO snapshot required for schema 003",
    )
    args = parser.parse_args()
    report = migrate_release_database(
        args.database,
        source_path=args.source,
        crosswalk_path=args.crosswalk,
        obo_path=args.obo,
    )
    print(json.dumps(report.__dict__, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
