import sqlite3
import tempfile
import json
from pathlib import Path
from types import SimpleNamespace

import networkx as nx
import pytest
import synepd.construct.build_release_db as build_mod

from synepd.construct.build_release_db import (
    build_release_database,
    extract_reaction_name,
    reaction_centers_are_isomorphic,
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
        graph.add_node(2, element="O", charge=0, atom_map=2)
        graph.add_edge(1, 2, order=(1, 1), standard_order=0)
        return graph, graph.copy(), "fake-wlhash"

    class FakeEditor:
        def apply(self, *args, **kwargs):
            return SimpleNamespace(step_reports=(), matches_product=True)

    monkeypatch.setattr(build_mod, "Standardize", FakeStandardize)
    monkeypatch.setattr(build_mod, "CanonRSMI", FakeCanon)
    monkeypatch.setattr(build_mod, "check_reaction_balance", ok_balance)
    monkeypatch.setattr(build_mod, "check_atom_map_balance", ok_atom_map_balance)
    monkeypatch.setattr(build_mod, "check_single_h_completion", ok_h_completion)
    monkeypatch.setattr(build_mod, "extract_graphs", fake_extract_graphs)
    monkeypatch.setattr(build_mod, "LWGEditor", FakeEditor)

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
                                "mode": "exact",
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

        report = build_release_database(
            json_path=json_path,
            hierarchy_path=hierarchy_path,
            db_path=db_path,
        )
        assert report.input_count == 1
        assert report.admitted_count == 1
        assert report.excluded_count == 0
        assert not report.enriched

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
        assert mode == "exact"
        assert json.loads(representation_json) == {
            "mode": "exact",
            "limitation": "Fixture representation note.",
            "atom_map_namespace": "curated_aam_key",
        }

        cursor.execute("SELECT DISTINCT graph_format FROM reaction_center;")
        assert cursor.fetchall() == [("synepd.node-link-json.zlib.v1",)]
        cursor.execute("SELECT DISTINCT graph_format FROM its;")
        assert cursor.fetchall() == [("synepd.node-link-json.zlib.v1",)]

        cursor.execute(
            "SELECT construction_version, graph_format, context_hash, "
            "events_json, diagnostics_json FROM mechanism_context;"
        )
        construction_version, graph_format, context_hash, events, diagnostics = (
            cursor.fetchone()
        )
        assert construction_version == "synepd.mechanistic-center.v1"
        assert graph_format == "synepd.node-link-json.zlib.v1"
        assert len(context_hash) == 64
        assert json.loads(events) == []
        assert json.loads(diagnostics)["epd_atom_maps"] == [1, 2]

        conn.close()

        original_database = db_path.read_bytes()

        class FailingEditor:
            def apply(self, *args, **kwargs):
                return SimpleNamespace(step_reports=(), matches_product=False)

        monkeypatch.setattr(build_mod, "LWGEditor", FailingEditor)
        with pytest.raises(ValueError, match="Curated EPD does not verify"):
            build_release_database(
                json_path=json_path,
                hierarchy_path=hierarchy_path,
                db_path=db_path,
            )
        assert db_path.read_bytes() == original_database


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


def test_reaction_center_isomorphism_uses_chemical_attributes():
    first = nx.Graph()
    first.add_node(
        1, element=("C", "C"), charge=(0, 1), lone_pairs=(0, 0), hcount=(3, 3)
    )
    second = nx.Graph()
    second.add_node(
        9, element=("N", "N"), charge=(0, 1), lone_pairs=(1, 0), hcount=(2, 2)
    )

    assert not reaction_centers_are_isomorphic(first, second)

    second.nodes[9].update(first.nodes[1])
    assert reaction_centers_are_isomorphic(first, second)
