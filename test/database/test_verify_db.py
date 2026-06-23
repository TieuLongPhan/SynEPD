from pathlib import Path

import pytest

from synepd.database.managers import EPDManager, TaxonomyManager
from synepd.database.models import SynEPDDatabase


def test_verify_current_database_statistics():
    db_path = Path("data/epdb.sqlite")
    if not db_path.exists():
        pytest.skip("data/epdb.sqlite has not been built")

    with SynEPDDatabase(db_path) as db:
        cursor = db.connection.cursor()

        expected_nonempty_tables = [
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
        for table in expected_nonempty_tables:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            count = cursor.fetchone()[0]
            assert count > 0, f"Table {table} is empty"

        cursor.execute(
            "SELECT COUNT(*) FROM taxon WHERE code = 'POLAR.99' OR code LIKE 'POLAR.99.%'"
        )
        assert cursor.fetchone()[0] == 0

        cursor.execute("PRAGMA integrity_check")
        assert cursor.fetchone()[0] == "ok"

        epd_mgr = EPDManager(db)
        assert isinstance(epd_mgr.get_reactions_by_first_arrow("LP-/Sigma+"), list)

        tax_mgr = TaxonomyManager(db)
        assert isinstance(tax_mgr.get_taxon_children("POLAR.01"), list)
