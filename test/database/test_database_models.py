import sqlite3
import tempfile
from pathlib import Path

from synepd.database.models import SynEPDDatabase


def test_create_tables():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.sqlite"
        with SynEPDDatabase(db_path) as db:
            db.create_tables()

            # Verify tables exist
            cursor = db.connection.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = {row[0] for row in cursor.fetchall()}

            expected_tables = {
                "dataset_release",
                "schema_migration",
                "reaction",
                "molecule",
                "reaction_component",
                "taxon",
                "reaction_taxonomy",
                "reaction_entry_code",
                "reaction_center",
                "its",
                "epd",
                "epd_arrow_type",
                "epd_arrow",
                "mechanism_context",
                "mechanistic_center",
                "sqlite_sequence",
            }
            assert expected_tables.issubset(tables)

            cursor.execute(
                "SELECT version, release_date, license FROM dataset_release;"
            )
            assert tuple(cursor.fetchone()) == ("v0.4.1", "2026-08-18", "CC BY 4.0")

            cursor.execute("SELECT version FROM schema_migration ORDER BY version;")
            assert [row[0] for row in cursor.fetchall()] == [
                "002_mechanism_context",
                "003_taxon_xref",
                "004_reaction_metadata",
                "005_mechanistic_center",
                "006_its_mechanistic_center",
                "007_core_release",
            ]

            cursor.execute("PRAGMA table_info(epd);")
            epd_columns = {row[1] for row in cursor.fetchall()}
            assert {"representation_mode", "representation_json"}.issubset(epd_columns)

            cursor.execute("PRAGMA table_info(reaction);")
            reaction_columns = {row[1] for row in cursor.fetchall()}
            assert "canonical_aam_key" in reaction_columns

            cursor.execute("PRAGMA table_info(mechanism_context);")
            context_columns = {row[1] for row in cursor.fetchall()}
            assert {
                "construction_version",
                "context_hash",
                "anchor_graph",
                "graph_format",
                "events_json",
                "diagnostics_json",
            }.issubset(context_columns)

            cursor.execute("PRAGMA table_info(mechanistic_center);")
            mc_columns = {row[1] for row in cursor.fetchall()}
            assert {
                "rc_id",
                "wlhash",
                "template_graph",
                "graph_format",
                "transition_edge_count",
                "rc_extension_edge_count",
                "transient_only_edge_count",
            }.issubset(mc_columns)

            cursor.execute("PRAGMA table_info(its);")
            its_columns = {row[1] for row in cursor.fetchall()}
            assert "mc_id" in its_columns
            assert "reaction_mechanistic_center" not in tables

            assert {
                "ontology_release",
                "taxon_xref",
                "reaction_alias",
                "reaction_relation",
            }.isdisjoint(tables)


def test_init_vocabulary():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.sqlite"
        with SynEPDDatabase(db_path) as db:
            db.create_tables()
            db.init_vocabulary()

            cursor = db.connection.cursor()
            cursor.execute("SELECT code FROM epd_arrow_type;")
            arrow_types = {row[0] for row in cursor.fetchall()}

            assert "LP-/Sigma+" in arrow_types
            assert "Sigma-/Sigma+" in arrow_types
            assert len(arrow_types) == 8


def test_foreign_keys_enforced():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.sqlite"
        with SynEPDDatabase(db_path) as db:
            db.create_tables()

            # Trying to insert a component pointing to a non-existent reaction should fail
            cursor = db.connection.cursor()
            try:
                cursor.execute(
                    "INSERT INTO reaction_component (reaction_id, molecule_id, side, component_index) VALUES (999, 999, 'reactant', 1);"
                )
                assert False, "Foreign key constraint failed to raise an error."
            except sqlite3.IntegrityError:
                assert True


def test_reaction_fts_uses_reaction_rowid():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.sqlite"
        with SynEPDDatabase(db_path) as db:
            db.create_tables()
            with db.connection:
                db.connection.execute("""
                    INSERT INTO reaction (case_id, canonical_rsmi, aam_key, name)
                    VALUES ('polar_000001', 'CCO>>CCO', 'mapped', 'Fischer esterification');
                    """)

            cursor = db.connection.cursor()
            cursor.execute("SELECT count(*) FROM reaction_fts;")
            assert cursor.fetchone()[0] == 1

            cursor.execute("""
                SELECT fts.rowid, fts.name, fts.case_id
                FROM reaction_fts fts
                WHERE reaction_fts MATCH 'Fischer';
                """)
            assert tuple(cursor.fetchone()) == (
                1,
                "Fischer esterification",
                "polar_000001",
            )
