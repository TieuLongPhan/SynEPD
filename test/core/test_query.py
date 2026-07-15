import json
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import networkx as nx

import synepd.construct.build_release_db as build_mod
import synepd.core.query as query_mod
from synepd.construct.build_release_db import build_release_database
from synepd.core.query import find_reactions_by_template, query_epd_by_reaction

MAPPED_RSMI = "[CH3:1][O-:2]>>[CH3:1][O:2]"
UNMAPPED_RSMI = "C[O-]>>CO"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


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


def fake_graphs(*args, **kwargs):
    graph = nx.Graph()
    graph.add_node(1, element="C", charge=0, atom_map=1)
    graph.add_node(2, element="O", charge=-1, atom_map=2)
    graph.add_edge(1, 2, order=1)
    return graph, graph.copy(), "fake-wlhash"


class FakeWLHash:
    def __init__(self, *args, **kwargs):
        pass

    def weisfeiler_lehman_graph_hash(self, graph):
        return "fake-wlhash"


class FakeEditor:
    def apply(self, *args, **kwargs):
        return SimpleNamespace(step_reports=(), matches_product=True)


def write_fixture_files(tmp_path: Path) -> tuple[Path, Path]:
    json_path = tmp_path / "polar_clean.json"
    hierarchy_path = tmp_path / "polar_hierarchy.json"

    json_path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "id": 1,
                        "family": "polar",
                        "tax_code": "POLAR.01.01.001",
                        "tax_codes": ["POLAR.01.01.001"],
                        "reaction_name": "Tiny methoxide example",
                        "reaction_names": ["Tiny methoxide example"],
                        "rsmi": UNMAPPED_RSMI,
                        "epd": [["LP-/Sigma+", [2], [1]]],
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
                        "name": "Bond polarization",
                    },
                    {
                        "code": "POLAR.01.01",
                        "parent_code": "POLAR.01",
                        "level": 3,
                        "name": "Transfer",
                    },
                    {
                        "code": "POLAR.01.01.001",
                        "parent_code": "POLAR.01.01",
                        "level": 4,
                        "name": "Tiny methoxide example",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    return json_path, hierarchy_path


def build_tiny_query_db(tmp_path, monkeypatch):
    monkeypatch.setattr(build_mod, "Standardize", FakeStandardize)
    monkeypatch.setattr(build_mod, "CanonRSMI", FakeCanon)
    monkeypatch.setattr(build_mod, "check_reaction_balance", ok_balance)
    monkeypatch.setattr(build_mod, "check_atom_map_balance", ok_atom_map_balance)
    monkeypatch.setattr(build_mod, "check_single_h_completion", ok_h_completion)
    monkeypatch.setattr(build_mod, "extract_graphs", fake_graphs)
    monkeypatch.setattr(build_mod, "LWGEditor", FakeEditor)
    monkeypatch.setattr(query_mod, "Standardize", FakeStandardize)
    monkeypatch.setattr(query_mod, "extract_graphs", fake_graphs)
    monkeypatch.setattr(
        "synkit.Graph.Feature.wl_hash.WLHash",
        FakeWLHash,
    )

    json_path, hierarchy_path = write_fixture_files(tmp_path)
    db_path = tmp_path / "test_query.sqlite"
    build_release_database(
        json_path=json_path, hierarchy_path=hierarchy_path, db_path=db_path
    )
    return db_path


def test_query_existing_reaction_is_fast_path(tmp_path, monkeypatch):
    db_path = build_tiny_query_db(tmp_path, monkeypatch)

    result = query_epd_by_reaction(UNMAPPED_RSMI, db_path)

    assert result["success"]
    assert result["path"] == 1
    assert result["case_id"] == "polar_000001"
    assert result["name"] == "Tiny methoxide example"
    assert result["arrows"] == [
        {
            "arrow_index": 1,
            "arrow_type_code": "LP-/Sigma+",
            "source_atoms": [2],
            "target_atoms": [1],
        }
    ]


def test_find_reactions_by_template_uses_reaction_center_index(tmp_path, monkeypatch):
    db_path = build_tiny_query_db(tmp_path, monkeypatch)

    reactions = find_reactions_by_template(MAPPED_RSMI, db_path)

    assert len(reactions) == 1
    assert reactions[0]["case_id"] == "polar_000001"
    assert reactions[0]["canonical_rsmi"] == UNMAPPED_RSMI


def test_template_projection_preserves_context_with_chemistry_aware_rc(tmp_path):
    db_path = tmp_path / "projection.sqlite"
    shutil.copyfile(REPOSITORY_ROOT / "data" / "epdb.sqlite", db_path)

    with sqlite3.connect(db_path) as connection:
        reaction_id, mapped_rsmi = connection.execute(
            "SELECT id, aam_key FROM reaction WHERE case_id = 'polar_000106'"
        ).fetchone()
        connection.execute(
            "UPDATE reaction SET canonical_rsmi = ? WHERE id = ?",
            ("__force_template_path__", reaction_id),
        )

    result = query_epd_by_reaction(mapped_rsmi, db_path)

    assert result["success"]
    assert result["path"] == 2
    assert not result["mechanism_ambiguous"]
    assert result["mechanism_candidate_count"] == 1
    candidate = result["mechanism_candidates"][0]
    assert candidate["reference_case_id"] == "polar_000106"
    assert candidate["verification"]["matches_product"]
    assert candidate["arrows"][1]["source_atoms"] == [5, 7]
    assert candidate["arrows"][1]["target_atoms"] == [5]
    assert candidate["arrows"][2]["source_atoms"] == [5]
    assert candidate["arrows"][2]["target_atoms"] == [5, 7]


def test_surrogate_projection_reports_real_mechanistic_ambiguity(tmp_path):
    db_path = tmp_path / "jones-projection.sqlite"
    shutil.copyfile(REPOSITORY_ROOT / "data" / "epdb.sqlite", db_path)

    with sqlite3.connect(db_path) as connection:
        reaction_id, mapped_rsmi = connection.execute(
            "SELECT id, aam_key FROM reaction WHERE case_id = 'polar_001538'"
        ).fetchone()
        connection.execute(
            "UPDATE reaction SET canonical_rsmi = ? WHERE id = ?",
            ("__force_template_path__", reaction_id),
        )

    result = query_epd_by_reaction(mapped_rsmi, db_path)

    assert result["success"]
    assert result["path"] == 2
    assert result["mechanism_ambiguous"]
    assert result["mechanism_candidate_count"] == 2
    assert all(
        candidate["verification"]["matches_product"]
        for candidate in result["mechanism_candidates"]
    )
    assert all(
        candidate["representation"]["mode"] == "closed_shell_surrogate"
        for candidate in result["mechanism_candidates"]
    )
