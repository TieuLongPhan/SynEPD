import json
import sqlite3

from synepd.core.graph_codec import GRAPH_FORMAT, encode_graph
from synepd.core.ingest import extract_graphs
from synepd.database.migrations import migrate_release_database
from synepd.database.models import SynEPDDatabase

RSMI = "[CH3:1][O-:2].[H+:3]>>[CH3:1][O:2][H:3]"
EPD = [["LP-/Sigma+", [2], [2, 3]]]


def test_source_assisted_migration_materializes_context(tmp_path):
    database_path = tmp_path / "legacy.sqlite"
    source_path = tmp_path / "polar.json"
    source_path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "id": 1,
                        "family": "polar",
                        "rsmi": RSMI,
                        "epd": EPD,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    its_graph, rc_graph, wlhash = extract_graphs(RSMI)

    with SynEPDDatabase(database_path) as database:
        database.create_tables()
        database.init_vocabulary()
        with database.connection:
            database.connection.execute("DELETE FROM schema_migration")
            reaction_id = database.connection.execute(
                """
                INSERT INTO reaction (case_id, canonical_rsmi, aam_key, name)
                VALUES (?, ?, ?, ?)
                """,
                ("polar_000001", "C[O-].[H+]>>CO", "legacy-canonical-aam", "Test"),
            ).lastrowid
            rc_id = database.connection.execute(
                """
                INSERT INTO reaction_center (
                    wlhash, template_graph, graph_format
                ) VALUES (?, ?, ?)
                """,
                (wlhash, encode_graph(rc_graph), GRAPH_FORMAT),
            ).lastrowid
            database.connection.execute(
                """
                INSERT INTO its (
                    reaction_id, rc_id, wlhash, graph_data, graph_format
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (reaction_id, rc_id, wlhash, encode_graph(its_graph), GRAPH_FORMAT),
            )
            database.connection.execute(
                "INSERT INTO epd (reaction_id, number_arrows) VALUES (?, 1)",
                (reaction_id,),
            )
            database.connection.execute(
                """
                INSERT INTO epd_arrow (
                    reaction_id, arrow_index, arrow_type_code,
                    source_atoms, target_atoms
                ) VALUES (?, 1, ?, ?, ?)
                """,
                (reaction_id, "LP-/Sigma+", "[2]", "[2, 3]"),
            )

    report = migrate_release_database(database_path, source_path=source_path)

    assert not report.already_applied
    assert report.context_count == 1
    with sqlite3.connect(database_path) as connection:
        aam_key, canonical_aam_key = connection.execute(
            "SELECT aam_key, canonical_aam_key FROM reaction"
        ).fetchone()
        assert aam_key == RSMI
        assert canonical_aam_key == "legacy-canonical-aam"
        assert (
            connection.execute("SELECT COUNT(*) FROM mechanism_context").fetchone()[0]
            == 1
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    second = migrate_release_database(database_path, source_path=source_path)
    assert second.already_applied
