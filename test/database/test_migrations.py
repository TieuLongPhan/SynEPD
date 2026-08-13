import json
import sqlite3
from pathlib import Path

import pytest

from synepd.core.graph_codec import GRAPH_FORMAT, encode_graph
from synepd.core.ingest import extract_graphs
from synepd.database.migrations import MigrationError, migrate_release_database
from synepd.database.models import SynEPDDatabase
from synepd.linkage import load_rxno_linkage

RSMI = "[CH3:1][O-:2].[H+:3]>>[CH3:1][O:2][H:3]"
EPD = [["LP-/Sigma+", [2], [2, 3]]]


def test_source_assisted_migration_materializes_context(tmp_path):
    database_path = tmp_path / "legacy.sqlite"
    source_path = tmp_path / "polar.json"
    crosswalk_path = tmp_path / "rxno_crosswalk.tsv"
    obo_path = tmp_path / "rxno.obo"
    repository_root = Path(__file__).resolve().parents[2]
    obo_path.write_bytes((repository_root / "data" / "rxno.obo").read_bytes())
    crosswalk_path.write_text(
        "taxon_code\ttaxon_level\ttaxon_name\trelation\tmatch_type\tscore\t"
        "ontology_id\tontology_name\n"
        "POLAR.02.01.020\t4\tMitsunobu reaction\tdcterms:isPartOf\tcurated\t"
        "1.0\tRXNO:0000034\tMitsunobu reaction\n",
        encoding="utf-8",
    )
    source_path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "id": 1,
                        "family": "polar",
                        "reaction_name": "Test",
                        "reaction_names": ["Test", "Migrated alias"],
                        "tax_code": "POLAR.02.01.020",
                        "tax_codes": ["POLAR.02.01.020"],
                        "entry_code": "POLAR.02.01.020.001",
                        "entry_codes": ["POLAR.02.01.020.001"],
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
            database.connection.execute(
                "INSERT INTO taxon (code, level, name) VALUES (?, ?, ?)",
                ("POLAR.02.01.020", 4, "Mitsunobu reaction"),
            )
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

    report = migrate_release_database(
        database_path,
        source_path=source_path,
        crosswalk_path=crosswalk_path,
        obo_path=obo_path,
    )

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
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "ontology_release",
            "taxon_xref",
            "reaction_alias",
            "reaction_relation",
        }.isdisjoint(tables)
        assert connection.execute(
            "SELECT taxon_code, entry_code, code_index, is_primary "
            "FROM reaction_entry_code"
        ).fetchone() == ("POLAR.02.01.020", "POLAR.02.01.020.001", 0, 1)
        assert [
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migration ORDER BY version"
            )
        ] == [
            "002_mechanism_context",
            "003_taxon_xref",
            "004_reaction_metadata",
            "005_mechanistic_center",
            "006_its_mechanistic_center",
            "007_core_release",
        ]
        assert (
            connection.execute("SELECT COUNT(*) FROM mechanistic_center").fetchone()[0]
            == 1
        )

        assert connection.execute("SELECT reaction_id, mc_id FROM its").fetchone() == (
            1,
            1,
        )
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'reaction_mechanistic_center'"
            ).fetchone()
            is None
        )

    xrefs, release = load_rxno_linkage(database_path)
    assert xrefs["POLAR.02.01.020"][0]["ontology_id"] == "RXNO:0000034"
    assert release and release["id"] == "rxno-2021-12-16"

    second = migrate_release_database(
        database_path,
        source_path=source_path,
        crosswalk_path=crosswalk_path,
        obo_path=obo_path,
    )
    assert second.already_applied


def test_database_stamped_at_003_upgrades_to_core_and_backfills_entry_codes(tmp_path):
    database_path = tmp_path / "release-at-003.sqlite"
    source_path = tmp_path / "polar.json"
    source_path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "id": 1,
                        "family": "polar",
                        "reaction_names": ["Stage", "Stage alias"],
                        "tax_code": "POLAR.01.01.001",
                        "tax_codes": ["POLAR.01.01.001"],
                        "entry_code": "POLAR.01.01.001.001",
                        "entry_codes": ["POLAR.01.01.001.001"],
                        "relations": [{"type": "component_of", "target_id": 2}],
                    },
                    {
                        "id": 2,
                        "family": "polar",
                        "reaction_names": ["Full reaction"],
                        "tax_code": "POLAR.01.01.001",
                        "tax_codes": ["POLAR.01.01.001"],
                        "entry_code": "POLAR.01.01.001.002",
                        "entry_codes": ["POLAR.01.01.001.002"],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    with SynEPDDatabase(database_path) as database:
        database.create_tables()
        with database.connection:
            database.connection.execute(
                "INSERT INTO taxon (code, level, name) VALUES (?, ?, ?)",
                ("POLAR.01.01.001", 4, "Example"),
            )
            database.connection.executemany(
                """
                INSERT INTO reaction (case_id, canonical_rsmi, aam_key, name)
                VALUES (?, ?, ?, ?)
                """,
                (
                    ("polar_000001", "CO>>CO", "mapped-1", "Stage"),
                    ("polar_000002", "CN>>CN", "mapped-2", "Full reaction"),
                ),
            )
            database.connection.execute("DROP TABLE reaction_entry_code")
            database.connection.execute(
                "DELETE FROM schema_migration WHERE version >= ?",
                ("004_reaction_metadata",),
            )

    report = migrate_release_database(database_path, source_path=source_path)

    assert not report.already_applied
    assert report.schema_version == "007_core_release"
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT checksum FROM schema_migration WHERE version = ?",
            ("003_taxon_xref",),
        ).fetchone() == (
            "sha256:c7e203b8593560864dc193719d2b6ef92afcc7d3ca2c62c30a7731b7d860c569",
        )
        assert connection.execute(
            "SELECT taxon_code, entry_code, is_primary FROM reaction_entry_code "
            "ORDER BY reaction_id"
        ).fetchall() == [
            ("POLAR.01.01.001", "POLAR.01.01.001.001", 1),
            ("POLAR.01.01.001", "POLAR.01.01.001.002", 1),
        ]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {"reaction_alias", "reaction_relation"}.isdisjoint(tables)
        assert [
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migration ORDER BY version"
            )
        ] == [
            "002_mechanism_context",
            "003_taxon_xref",
            "004_reaction_metadata",
            "005_mechanistic_center",
            "006_its_mechanistic_center",
            "007_core_release",
        ]


def test_pending_004_nonempty_database_requires_complete_source(tmp_path):
    database_path = tmp_path / "release-at-003.sqlite"
    source_path = tmp_path / "incomplete.json"
    source_path.write_text('{"records": []}\n', encoding="utf-8")

    with SynEPDDatabase(database_path) as database:
        database.create_tables()
        with database.connection:
            database.connection.execute(
                "INSERT INTO reaction (case_id, canonical_rsmi, aam_key) "
                "VALUES ('polar_000001', 'CO>>CO', 'mapped')"
            )
            database.connection.execute(
                "DELETE FROM schema_migration WHERE version = ?",
                ("004_reaction_metadata",),
            )

    with pytest.raises(MigrationError, match="requires source JSON"):
        migrate_release_database(database_path)
    with pytest.raises(MigrationError, match="source coverage mismatch"):
        migrate_release_database(database_path, source_path=source_path)

    with sqlite3.connect(database_path) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM schema_migration WHERE version = ?",
                ("004_reaction_metadata",),
            ).fetchone()
            is None
        )


def test_create_tables_does_not_stamp_004_on_populated_003_database(tmp_path):
    database_path = tmp_path / "release-at-003.sqlite"
    with SynEPDDatabase(database_path) as database:
        database.create_tables()
        with database.connection:
            database.connection.execute(
                "INSERT INTO reaction (case_id, canonical_rsmi, aam_key) "
                "VALUES ('polar_000001', 'CO>>CO', 'mapped')"
            )
            database.connection.execute(
                "DELETE FROM schema_migration WHERE version = ?",
                ("004_reaction_metadata",),
            )

    with SynEPDDatabase(database_path) as database:
        database.create_tables()
        assert (
            database.connection.execute(
                "SELECT 1 FROM schema_migration WHERE version = ?",
                ("004_reaction_metadata",),
            ).fetchone()
            is None
        )
