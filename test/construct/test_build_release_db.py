import sqlite3
import tempfile
import json
from pathlib import Path
from types import SimpleNamespace

import networkx as nx
import synepd.construct.build_release_db as build_mod

from synepd.construct.build_release_db import (
    build_release_database,
    extract_reaction_name,
)


def test_build_release_database_with_clean_records(monkeypatch):
    class FakeStandardize:
        def fit(self, rsmi):
            return rsmi

    class FakeCanon:
        def __init__(self, *args, **kwargs):
            pass

        def canonicalise(self, rsmi):
            return SimpleNamespace(canonical_rsmi=rsmi)

    def ok_balance(*args, **kwargs):
        return SimpleNamespace(balanced=True)

    def ok_atom_map_balance(*args, **kwargs):
        return SimpleNamespace(is_balanced=True)

    def ok_h_completion(*args, **kwargs):
        return True, None

    def fake_extract_graphs(*args, **kwargs):
        graph = nx.Graph()
        graph.add_node(1, element="C", charge=0, atom_map=1)
        return graph, graph.copy(), "fake-wlhash"

    monkeypatch.setattr(build_mod, "Standardize", FakeStandardize)
    monkeypatch.setattr(build_mod, "CanonRSMI", FakeCanon)
    monkeypatch.setattr(build_mod, "check_reaction_balance", ok_balance)
    monkeypatch.setattr(build_mod, "check_atom_map_balance", ok_atom_map_balance)
    monkeypatch.setattr(build_mod, "check_single_h_completion", ok_h_completion)
    monkeypatch.setattr(build_mod, "extract_graphs", fake_extract_graphs)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        json_path = tmp / "polar_clean.json"
        hierarchy_path = tmp / "polar_hierarchy.json"
        db_path = Path(tmpdir) / "test_release.sqlite"
        json_path.write_text(
            json.dumps(
                {
                    "records": [
                        {
                            "id": 1,
                            "family": "polar",
                            "tax_code": "POLAR.01.01.001",
                            "tax_codes": ["POLAR.01.01.001", "POLAR.01.01.002"],
                            "reaction_name": "Example",
                            "reaction_names": ["Example", "Alias example"],
                            "rsmi": "CCO>>CCO",
                            "epd": [["LP-/Sigma+", [1], [1, 2]]],
                            "epd_representation": {
                                "mode": "closed_shell_surrogate",
                                "limitation": "Fixture representation note.",
                            },
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        hierarchy_path.write_text(
            json.dumps(
                {
                    "taxons": [
                        {
                            "code": "POLAR",
                            "parent_code": None,
                            "level": 1,
                            "name": "Polar",
                        },
                        {
                            "code": "POLAR.01",
                            "parent_code": "POLAR",
                            "level": 2,
                            "name": "Class",
                        },
                        {
                            "code": "POLAR.01.01",
                            "parent_code": "POLAR.01",
                            "level": 3,
                            "name": "Subclass",
                        },
                        {
                            "code": "POLAR.01.01.001",
                            "parent_code": "POLAR.01.01",
                            "level": 4,
                            "name": "Example",
                        },
                        {
                            "code": "POLAR.01.01.002",
                            "parent_code": "POLAR.01.01",
                            "level": 4,
                            "name": "Alias example",
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )

        build_release_database(
            json_path=json_path,
            hierarchy_path=hierarchy_path,
            db_path=db_path,
        )

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM taxon;")
        assert cursor.fetchone()[0] == 5

        cursor.execute("SELECT COUNT(*) FROM reaction;")
        assert cursor.fetchone()[0] == 1

        cursor.execute(
            "SELECT case_id, name FROM reaction WHERE case_id = 'polar_000001';"
        )
        row = cursor.fetchone()
        assert row is not None
        assert row[1] == "Example"

        cursor.execute("SELECT taxon_code FROM reaction_taxonomy ORDER BY taxon_code;")
        assert [r[0] for r in cursor.fetchall()] == [
            "POLAR.01.01.001",
            "POLAR.01.01.002",
        ]

        cursor.execute("SELECT representation_mode, representation_json FROM epd;")
        mode, representation_json = cursor.fetchone()
        assert mode == "closed_shell_surrogate"
        assert json.loads(representation_json) == {
            "mode": "closed_shell_surrogate",
            "limitation": "Fixture representation note.",
        }

        conn.close()


def test_extract_reaction_name():
    # Verify exact match with normal names
    assert (
        extract_reaction_name("polar01_001_alcohol_protonation_deprotonation")
        == "Alcohol protonation deprotonation"
    )
    # Verify spelling fixes
    assert (
        extract_reaction_name("polar01_001_alcohol_protonation_deprptonation")
        == "Alcohol protonation deprotonation"
    )
    assert (
        extract_reaction_name("polar01_020_nitro_aci_nitro_tautomerizaton")
        == "Nitro aci nitro tautomerization"
    )
    # Verify workup suffix removal and direct mapping
    assert (
        extract_reaction_name(
            "polar06_699_dissolving_metal_carbonyl_reduction_polar_workup"
        )
        == "Alcohol protonation deprotonation"
    )
    assert (
        extract_reaction_name("polar08_864_acyloin_condensation_polar_workup_sequence")
        == "Acyloin condensation"
    )
    # Verify fallback behavior for short case IDs
    assert extract_reaction_name("simple_case") == "Simple case"
