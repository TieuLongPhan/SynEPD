import json
from pathlib import Path
from types import SimpleNamespace

import networkx as nx

import synepd.construct.build_release_db as build_mod
import synepd.core.query as query_mod
from synepd.construct.build_release_db import build_release_database
from synepd.core.query import find_reactions_by_template, query_epd_by_reaction

MAPPED_RSMI = "[CH3:1][O-:2]>>[CH3:1][O:2]"
UNMAPPED_RSMI = "C[O-]>>CO"


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
