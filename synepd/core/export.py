import json
from pathlib import Path
from typing import Dict, Any

from synepd.database.models import SynEPDDatabase


def export_taxonomy_tree(db: SynEPDDatabase) -> Dict[str, Any]:
    """Export the entire taxonomy and attached reactions as a nested JSON tree."""
    cursor = db.connection.cursor()

    # 1. Fetch all taxons
    cursor.execute(
        "SELECT code, parent_code, level, name FROM taxon ORDER BY level, code;"
    )
    taxons = cursor.fetchall()

    # 2. Fetch all reactions mapped to taxon
    cursor.execute("""
        SELECT rt.taxon_code, r.case_id, r.canonical_rsmi
        FROM reaction_taxonomy rt
        JOIN reaction r ON r.id = rt.reaction_id
    """)
    reactions = cursor.fetchall()

    # Group reactions by taxon
    rxn_by_taxon = {}
    for rxn in reactions:
        tcode = rxn["taxon_code"]
        if tcode not in rxn_by_taxon:
            rxn_by_taxon[tcode] = []
        rxn_by_taxon[tcode].append(
            {"case_id": rxn["case_id"], "canonical_rsmi": rxn["canonical_rsmi"]}
        )

    # Build tree
    # Nodes dict mapping code to its node dict
    nodes = {}
    root_nodes = []

    for t in taxons:
        code = t["code"]
        node = {
            "code": code,
            "name": t["name"],
            "level": t["level"],
            "children": [],
            "reactions": rxn_by_taxon.get(code, []),
        }
        nodes[code] = node

        parent = t["parent_code"]
        if parent is None:
            root_nodes.append(node)
        else:
            if parent in nodes:
                nodes[parent]["children"].append(node)

    return {"taxonomy": root_nodes}


def write_export_to_file(db_path: Path, output_path: Path) -> None:
    db = SynEPDDatabase(db_path)
    tree = export_taxonomy_tree(db)
    db.close()

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(tree, f, indent=2)


if __name__ == "__main__":
    write_export_to_file(Path("data/epdb.sqlite"), Path("export_demo.json"))
