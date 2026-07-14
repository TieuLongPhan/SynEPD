import json
import tempfile
from pathlib import Path
import networkx as nx
from synepd.core.ingest import (
    _set_reaction_center_standard_order,
    parse_hierarchy,
    strip_atom_map,
    extract_graphs,
    parse_epd,
)


def test_parse_hierarchy():
    hierarchy_md = """
## POLAR — Polar Reactions
### POLAR.01 — Subcategory 1
- **POLAR.01.01 — Detail Level 1 polar workup**
  - `POLAR.01.01.01 — Leaf Node 1 polar workup sequence (5 cases)`
"""
    with tempfile.TemporaryDirectory() as tmpdir:
        md_path = Path(tmpdir) / "hierarchy.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(hierarchy_md)

        res = parse_hierarchy(md_path)
        assert res["POLAR"] == "Polar Reactions"
        assert res["POLAR.01"] == "Subcategory 1"
        assert res["POLAR.01.01"] == "Detail Level 1"
        assert res["POLAR.01.01.01"] == "Leaf Node 1"


def test_strip_atom_map():
    mapped_smiles = "[CH3:1][OH:2]"
    stripped = strip_atom_map(mapped_smiles)
    assert "1" not in stripped
    assert "2" not in stripped
    assert stripped == "CO"


def test_extract_graphs():
    # A simple mapped reaction SMILES
    rsmi = "[CH3:1][O-:2].[H+:3]>>[CH3:1][OH:2]"
    res = extract_graphs(rsmi)
    assert res is not None
    its_graph, rc_graph, wlhash = res
    assert its_graph is not None
    assert rc_graph is not None
    assert isinstance(wlhash, str)
    assert len(wlhash) > 0


def test_extract_graphs_uses_kekule_order_for_reaction_center():
    rsmi = (
        "[O:1]([c:7]1[cH:5][cH:2][cH:4][cH:3][cH:6]1)[H:8]>>"
        "[O:1]=[C:7]1[CH:5]([H:8])[CH:2]=[CH:4][CH:3]=[CH:6]1"
    )

    kekule_result = extract_graphs(rsmi)
    order_result = extract_graphs(rsmi, reaction_center_bond_order="order")

    assert kekule_result is not None
    assert order_result is not None
    _, kekule_center, _ = kekule_result
    _, order_center, _ = order_result
    assert set(kekule_center.nodes) == {1, 5, 7, 8}
    assert set(order_center.nodes) == {1, 2, 3, 4, 5, 6, 7, 8}


def test_kekule_reaction_center_ignores_unchanged_aromatic_bonds():
    its_graph = nx.Graph()
    its_graph.add_edge(1, 2, order=(1.5, 1.5), kekule_order=(1.0, 2.0))
    its_graph.add_edge(2, 3, order=(1.5, 1.0), kekule_order=(2.0, 1.0))

    _set_reaction_center_standard_order(its_graph, "kekule_order")

    assert its_graph.edges[1, 2]["standard_order"] == 0.0
    assert its_graph.edges[2, 3]["standard_order"] == 1.0


def test_parse_epd():
    ground_truth = [["LP-/Sigma+", [2], [2, 3]], ["Sigma-/LP+", [1, 2], [1]]]
    arrows = parse_epd(ground_truth)
    assert len(arrows) == 2
    assert arrows[0]["arrow_index"] == 1
    assert arrows[0]["arrow_type_code"] == "LP-/Sigma+"
    assert json.loads(arrows[0]["source_atoms"]) == [2]
    assert json.loads(arrows[0]["target_atoms"]) == [2, 3]
