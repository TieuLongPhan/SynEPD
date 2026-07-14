import json
import re
import gzip
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rdkit import Chem
from synkit.IO import rsmi_to_graph
from synkit.Graph.ITS.its_construction import ITSConstruction
from synkit.Graph.ITS.rc_extractor import RCExtractor
from synkit.Graph.Feature.wl_hash import WLHash

HEADING_RE = re.compile(
    r"^#{1,4}\s+((?:POLAR\.)?\d{2}(?:\.\d{2})?(?:\.\d{3})?|POLAR)\s+[—–-]\s+(.+?)\s*$"
)


def clean_taxon_name(name: str) -> str:
    name_lower = name.lower()
    if name_lower.endswith(" polar workup sequence"):
        return name[:-22].strip()
    elif name_lower.endswith(" polar workup"):
        return name[:-13].strip()
    return name.strip()


def normalize_taxon_code(code: str) -> str:
    return code if code.startswith("POLAR") else f"POLAR.{code}"


def parse_hierarchy(path: Path) -> dict[str, str]:
    path = Path(path)
    if path.suffix == ".json" or path.suffixes[-2:] == [".json", ".gz"]:
        return {
            t["code"]: clean_taxon_name(t["name"]) for t in load_hierarchy_taxons(path)
        }

    hierarchy = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            heading = HEADING_RE.match(line)
            if heading:
                code, name = heading.groups()
                code = normalize_taxon_code(code.strip())
                if code != "POLAR.99" and not code.startswith("POLAR.99."):
                    hierarchy[code] = clean_taxon_name(name)
                continue
            if line.startswith("## "):
                parts = re.split(r" [—–-] ", line[3:], maxsplit=1)
                if len(parts) == 2:
                    code = normalize_taxon_code(parts[0].strip())
                    if code != "POLAR.99" and not code.startswith("POLAR.99."):
                        hierarchy[code] = clean_taxon_name(parts[1])
            elif line.startswith("### "):
                parts = re.split(r" [—–-] ", line[4:], maxsplit=1)
                if len(parts) == 2:
                    code = normalize_taxon_code(parts[0].strip())
                    if code != "POLAR.99" and not code.startswith("POLAR.99."):
                        hierarchy[code] = clean_taxon_name(parts[1])
            elif line.startswith("- **"):
                content = line[4:].split("**", 1)
                if len(content) == 2:
                    parts = re.split(r" [—–-] ", content[0], maxsplit=1)
                    if len(parts) == 2:
                        code = normalize_taxon_code(parts[0].strip())
                        if code != "POLAR.99" and not code.startswith("POLAR.99."):
                            hierarchy[code] = clean_taxon_name(parts[1])
            elif "  - `" in line or line.strip().startswith("- `"):
                cleaned = line.replace("  - `", "").replace("- `", "").replace("`", "")
                parts = re.split(r" [—–-] ", cleaned, maxsplit=1)
                if len(parts) == 2:
                    code = normalize_taxon_code(parts[0].strip())
                    name = parts[1].split(" (", 1)[0].strip()
                    if code != "POLAR.99" and not code.startswith("POLAR.99."):
                        hierarchy[code] = clean_taxon_name(name)
    return hierarchy


def load_hierarchy_taxons(path: Path) -> list[dict[str, Any]]:
    path = Path(path)
    if path.suffix == ".json" or path.suffixes[-2:] == [".json", ".gz"]:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as f:
            data = json.load(f)
        raw_taxons = data.get("taxons", data) if isinstance(data, dict) else data
        taxons = []
        for item in raw_taxons:
            code = item["code"]
            taxons.append(
                {
                    "code": code,
                    "parent_code": item.get("parent_code"),
                    "level": int(item.get("level", len(code.split(".")))),
                    "name": clean_taxon_name(item["name"]),
                }
            )
        return taxons

    hierarchy = parse_hierarchy(path)
    taxons = []
    for code, name in hierarchy.items():
        taxons.append(
            {
                "code": code,
                "parent_code": ".".join(code.split(".")[:-1]) if "." in code else None,
                "level": len(code.split(".")),
                "name": name,
            }
        )
    return sorted(taxons, key=lambda t: (t["level"], t["code"]))


def strip_atom_map(smiles: str) -> str:
    mol = Chem.MolFromSmiles(smiles, sanitize=False)
    if mol is None:
        return smiles
    for atom in mol.GetAtoms():
        atom.SetAtomMapNum(0)
    try:
        Chem.SanitizeMol(mol)
        mol = Chem.RemoveHs(mol)
    except Exception:
        pass
    return Chem.MolToSmiles(mol, canonical=True)


def extract_graphs(
    rsmi: str, *, reaction_center_bond_order: str = "kekule_order"
) -> Optional[Tuple[Any, Any, str]]:
    """Extract ITS and reaction-center graphs from a mapped reaction SMILES.

    Reaction-center membership is derived from Kekulé bond orders by default.
    This avoids treating a representation-only aromatic ``1.5`` to ``1`` or
    ``2`` bond change as a reaction-center change. Pass ``"order"`` to retain
    the legacy aromatic-order behavior.
    """
    if reaction_center_bond_order not in {"kekule_order", "order"}:
        raise ValueError("reaction_center_bond_order must be 'kekule_order' or 'order'")
    try:
        r_graph, p_graph = rsmi_to_graph(rsmi, drop_non_aam=True)
        if r_graph is None or p_graph is None:
            return None

        its_graph = ITSConstruction().construct(r_graph, p_graph)
        _set_reaction_center_standard_order(its_graph, reaction_center_bond_order)
        rc_graph = RCExtractor().extract(its_graph)
        rc_graph.graph["reaction_center_bond_order"] = reaction_center_bond_order
        wlhash = WLHash(iterations=3).weisfeiler_lehman_graph_hash(rc_graph)
        return its_graph, rc_graph, wlhash
    except Exception:
        return None


def _set_reaction_center_standard_order(
    its_graph: Any, bond_order_attribute: str
) -> None:
    """Set ITS bond-change values from the requested paired bond order.

    Two aromatic bonds can receive different arbitrary Kekulé assignments while
    retaining the same aromatic bond order on both reaction sides. Such a
    ``1.5 -> 1.5`` change is representational, not chemical, and must not add
    its ring atoms to the reaction center.
    """
    for _, _, attributes in its_graph.edges(data=True):
        reactant_order, product_order = attributes[bond_order_attribute]
        standard_order = reactant_order - product_order
        if bond_order_attribute == "kekule_order" and attributes.get("order") == (
            1.5,
            1.5,
        ):
            standard_order = 0.0
        attributes["standard_order"] = standard_order
    its_graph.graph["reaction_center_bond_order"] = bond_order_attribute


def parse_epd(ground_truth: List[list]) -> List[Dict[str, Any]]:
    arrows = []
    for i, step in enumerate(ground_truth):
        arrow_type = step[0]
        source_atoms = step[1]
        target_atoms = step[2]
        arrows.append(
            {
                "arrow_index": i + 1,
                "arrow_type_code": arrow_type,
                "source_atoms": json.dumps(source_atoms),
                "target_atoms": json.dumps(target_atoms),
            }
        )
    return arrows
