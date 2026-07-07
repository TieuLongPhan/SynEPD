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
                "reaction",
                "molecule",
                "reaction_component",
                "taxon",
                "reaction_taxonomy",
                "reaction_center",
                "its",
                "epd",
                "epd_arrow_type",
                "epd_arrow",
                "sqlite_sequence",
            }
            assert expected_tables.issubset(tables)

            cursor.execute(
                "SELECT version, release_date, license FROM dataset_release;"
            )
            assert tuple(cursor.fetchone()) == ("v0.1.0", "2026-07-07", "CC BY 4.0")


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
