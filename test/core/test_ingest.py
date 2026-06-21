import json
import tempfile
from pathlib import Path
from synepd.core.ingest import (
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


def test_parse_epd():
    ground_truth = [["LP-/Sigma+", [2], [2, 3]], ["Sigma-/LP+", [1, 2], [1]]]
    arrows = parse_epd(ground_truth)
    assert len(arrows) == 2
    assert arrows[0]["arrow_index"] == 1
    assert arrows[0]["arrow_type_code"] == "LP-/Sigma+"
    assert json.loads(arrows[0]["source_atoms"]) == [2]
    assert json.loads(arrows[0]["target_atoms"]) == [2, 3]
