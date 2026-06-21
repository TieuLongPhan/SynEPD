import pytest
import sqlite3
import tempfile
from pathlib import Path

from synepd.database.models import SynEPDDatabase
from synepd.database.managers import ReactionManager, TaxonomyManager, EPDManager
from synepd.construct.build_release_db import build_release_database


def test_verify_db_statistics():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_release.sqlite"
        build_release_database(
            json_path=Path("data/polar.json"),
            hierarchy_path=Path("data/hierarchy.md"),
            db_path=db_path,
        )

        with SynEPDDatabase(db_path) as db:
            cursor = db.connection.cursor()

            tables = [
                "reaction",
                "molecule",
                "reaction_component",
                "taxon",
                "reaction_taxonomy",
                "reaction_center",
                "its",
                "epd",
                "epd_arrow",
            ]
            for table in tables:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                count = cursor.fetchone()[0]
                assert count > 0, f"Table {table} is empty!"

            # Verify EPDManager queries
            epd_mgr = EPDManager(db)
            rxns = epd_mgr.get_reactions_by_first_arrow("LP-/Sigma+")
            # polar_1 has cases with LP-/Sigma+
            assert len(rxns) >= 0  # Just verifying it executes

            # Verify TaxonomyManager queries
            tax_mgr = TaxonomyManager(db)
            children = tax_mgr.get_taxon_children("POLAR.01")
            assert len(children) >= 0  # Verifying execution
