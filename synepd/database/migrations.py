"""Transactional release-database migrations.

The v0.1 to v0.2 migration is deliberately offline: it derives safe graph
payloads and EPD-aware centers only from data already present in the database.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3

from synkit.Graph.Mech import LWGEditor
from synkit.Graph.ITS.rc_extractor import RCExtractor

from synepd.core.graph_codec import GRAPH_FORMAT, decode_graph, encode_graph
from synepd.core.ingest import extract_graphs
from synepd.core.mechanism import (
    MECHANISM_CONTEXT_VERSION,
    build_mechanistic_center_from_graphs,
    serialize_mechanism_context,
)
from synepd.core.representation import (
    remap_epd,
    remap_representation,
    representation_verification_rsmi,
)
from synepd.database.models import (
    CURRENT_SCHEMA_CHECKSUM,
    CURRENT_SCHEMA_VERSION,
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
    already_applied: bool = False


class MigrationError(RuntimeError):
    """Raised when an atomic database migration cannot be completed."""


def migrate_release_database(
    path: Path | str, *, source_path: Path | str | None = None
) -> MigrationReport:
    """Upgrade a SQLite release database to the current safe schema."""
    database_path = Path(path)
    source_records = _load_source_records(source_path)
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        connection.execute("BEGIN IMMEDIATE")
        _ensure_migration_tables(connection)
        applied = connection.execute(
            "SELECT checksum FROM schema_migration WHERE version = ?",
            (CURRENT_SCHEMA_VERSION,),
        ).fetchone()
        if applied:
            if applied[0] != CURRENT_SCHEMA_CHECKSUM:
                raise MigrationError(
                    f"Migration checksum mismatch for {CURRENT_SCHEMA_VERSION}"
                )
            context_count = connection.execute(
                "SELECT COUNT(*) FROM mechanism_context"
            ).fetchone()[0]
            reaction_count = connection.execute(
                "SELECT COUNT(*) FROM reaction"
            ).fetchone()[0]
            connection.commit()
            return MigrationReport(
                schema_version=CURRENT_SCHEMA_VERSION,
                reaction_count=reaction_count,
                context_count=context_count,
                representation_count=0,
                epd_repair_count=0,
                reaction_center_graph_count=0,
                its_graph_count=0,
                already_applied=True,
            )

        report = _apply_mechanism_context_migration(connection, source_records)
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise MigrationError(
                f"Foreign-key validation failed with {len(violations)} violation(s)"
            )
        connection.execute(
            """
            INSERT INTO schema_migration (version, applied_at, checksum)
            VALUES (?, datetime('now'), ?)
            """,
            (CURRENT_SCHEMA_VERSION, CURRENT_SCHEMA_CHECKSUM),
        )
        connection.commit()
        return report
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _ensure_migration_tables(connection: sqlite3.Connection) -> None:
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
        schema_version=CURRENT_SCHEMA_VERSION,
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
    args = parser.parse_args()
    report = migrate_release_database(args.database, source_path=args.source)
    print(json.dumps(report.__dict__, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
