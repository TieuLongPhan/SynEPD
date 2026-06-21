import json
import tempfile
from pathlib import Path
from synepd.database.models import SynEPDDatabase
from synepd.core.export import export_taxonomy_tree, write_export_to_file


def test_export_taxonomy_tree():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_export.sqlite"
        with SynEPDDatabase(db_path) as db:
            db.create_tables()
            # Populate tables
            db.connection.execute(
                "INSERT INTO taxon (code, parent_code, level, name) VALUES (?, ?, ?, ?)",
                ("POLAR", None, 1, "Polar Root"),
            )
            db.connection.execute(
                "INSERT INTO reaction (case_id, canonical_rsmi, aam_key) VALUES (?, ?, ?)",
                ("polar01_001", "CC[O-].[NH4+]>>CCO", "AAM_KEY"),
            )
            db.connection.execute(
                "INSERT INTO reaction_taxonomy (reaction_id, taxon_code) VALUES (?, ?)",
                (1, "POLAR"),
            )
            db.connection.commit()

            tree = export_taxonomy_tree(db)
            assert "taxonomy" in tree
            assert len(tree["taxonomy"]) == 1
            assert tree["taxonomy"][0]["code"] == "POLAR"
            assert len(tree["taxonomy"][0]["reactions"]) == 1

        # Test write export to file
        out_path = Path(tmpdir) / "export.json"
        write_export_to_file(db_path, out_path)
        assert out_path.exists()
        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            assert data["taxonomy"][0]["code"] == "POLAR"
