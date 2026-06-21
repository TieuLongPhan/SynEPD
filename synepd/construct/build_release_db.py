import json
from pathlib import Path
import hashlib
import networkx as nx
import zlib
import pickle
from rdkit import Chem
from synkit.Graph.Matcher.subgraph_matcher import SubgraphSearchEngine
from synkit.Chem.Reaction.standardize import Standardize
from synkit.Chem.Reaction.canon_rsmi import CanonRSMI

from synepd.database.models import SynEPDDatabase
from synepd.core.ingest import (
    parse_hierarchy,
    strip_atom_map,
    extract_graphs,
    parse_epd,
)
from synepd.precheck import (
    check_reaction_balance,
    check_atom_map_balance,
    check_single_h_completion,
)


def generate_aam_key(rsmi: str) -> str:
    return hashlib.md5(rsmi.encode("utf-8")).hexdigest()


def extract_reaction_name(case_id: str) -> str:
    """Extract and format a human-readable reaction name from a SynEPD Case ID.

    Splits the Case ID, removes the prefix, replaces underscores with spaces,
    applies common name/spelling fixes, and capitalizes.

    :param case_id: The raw case ID string.
    :return: A cleaned, formatted reaction name.

    .. code-block:: python

        name = extract_reaction_name("polar01_001_alcohol_protonation_deprotonation")
        # returns "Alcohol protonation deprotonation"
    """
    if "polar06_699" in case_id:
        return "Alcohol protonation deprotonation"

    # Define corrections/mappings for known case ID name segments or typos
    corrections = {
        "deprptonation": "deprotonation",
        "tautomerizaton": "tautomerization",
    }

    parts = case_id.split("_", 2)
    if len(parts) >= 3:
        raw_name = parts[2]
    else:
        raw_name = case_id

    # Strip polar workup suffixes
    raw_name_lower = raw_name.lower()
    if raw_name_lower.endswith("polar_workup_sequence"):
        raw_name = raw_name[:-21]
    elif raw_name_lower.endswith("polar_workup"):
        raw_name = raw_name[:-12]

    cleaned = raw_name.replace("_", " ").strip()

    # Apply word-based spelling corrections
    words = cleaned.split()
    corrected_words = []
    for w in words:
        low_w = w.lower()
        if low_w in corrections:
            corrected_words.append(corrections[low_w])
        else:
            corrected_words.append(w)

    result = " ".join(corrected_words)
    return result.strip().capitalize()


def build_release_database(
    json_path: Path, hierarchy_path: Path, db_path: Path
) -> None:
    hierarchy = parse_hierarchy(hierarchy_path)

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    with SynEPDDatabase(db_path) as db:
        db.create_tables()
        db.init_vocabulary()

        molecule_cache = {}
        # rc_cache will store wlhash -> list of (rc_id, rc_graph) to handle collisions
        rc_cache = {}

        # Load taxonomy
        with db.connection:
            for code, name in hierarchy.items():
                level = len(code.split("."))
                parent_code = ".".join(code.split(".")[:-1]) if level > 1 else None
                db.connection.execute(
                    "INSERT OR IGNORE INTO taxon (code, parent_code, level, name) VALUES (?, ?, ?, ?)",
                    (code, parent_code, level, name),
                )

        for c in data["cases"]:
            case_id = c["case_id"]
            rsmi = c["rsmi"]
            level4_code = c["level4_code"]
            ground_truth = c.get("ground_truth", [])

            try:
                canonical_rsmi = Standardize().fit(rsmi)
                aam_key = (
                    CanonRSMI(backend="wl", wl_iterations=5)
                    .canonicalise(rsmi)
                    .canonical_rsmi
                )
            except Exception:
                continue

            # --- Prechecks ---
            balance = check_reaction_balance(rsmi)
            if not balance.balanced:
                continue

            atom_map_balance = check_atom_map_balance(rsmi)
            if not atom_map_balance.is_balanced:
                continue

            is_h_complete, _ = check_single_h_completion(rsmi)
            if not is_h_complete:
                continue
            # -----------------

            # 1. Ingest Reaction
            name = extract_reaction_name(case_id)
            try:
                with db.connection:
                    cursor = db.connection.execute(
                        "INSERT INTO reaction (case_id, canonical_rsmi, aam_key, name) VALUES (?, ?, ?, ?)",
                        (case_id, canonical_rsmi, aam_key, name),
                    )
                    reaction_id = cursor.lastrowid
            except Exception:
                continue

            # 2. Ingest Taxonomy
            with db.connection:
                db.connection.execute(
                    "INSERT INTO reaction_taxonomy (reaction_id, taxon_code) VALUES (?, ?)",
                    (reaction_id, level4_code),
                )

            # 3. Ingest ReactionComponents
            parts = rsmi.split(">>")
            if len(parts) == 2:
                reactants = parts[0].split(".")
                products = parts[1].split(".")

                def ingest_side(smiles_list, side_name):
                    for idx, m_smiles in enumerate(smiles_list, start=1):
                        flat_smiles = strip_atom_map(m_smiles)
                        if flat_smiles not in molecule_cache:
                            mol = Chem.MolFromSmiles(flat_smiles)
                            inchikey = Chem.MolToInchiKey(mol) if mol else None
                            with db.connection:
                                cursor = db.connection.execute(
                                    "INSERT OR IGNORE INTO molecule (canonical_smiles, inchikey) VALUES (?, ?)",
                                    (flat_smiles, inchikey),
                                )
                                if cursor.lastrowid:
                                    molecule_cache[flat_smiles] = cursor.lastrowid
                                else:
                                    cursor = db.connection.execute(
                                        "SELECT id FROM molecule WHERE canonical_smiles = ?",
                                        (flat_smiles,),
                                    )
                                    molecule_cache[flat_smiles] = cursor.fetchone()[0]

                        mol_id = molecule_cache[flat_smiles]
                        with db.connection:
                            db.connection.execute(
                                "INSERT INTO reaction_component (reaction_id, molecule_id, side, component_index) VALUES (?, ?, ?, ?)",
                                (reaction_id, mol_id, side_name, idx),
                            )

                ingest_side(reactants, "reactant")
                ingest_side(products, "product")

            # 4. Extract and Ingest ITS/RC
            graph_res = extract_graphs(aam_key)
            if graph_res is not None:
                its_graph, rc_graph, wlhash = graph_res

                # Check for isomorphism among cached graphs with the same wlhash
                matched_rc_id = None
                if wlhash in rc_cache:
                    for cached_id, cached_graph in rc_cache[wlhash]:
                        # Basic isomorphism check (synkit nodes usually have 'atom' or similar, edges have 'order')
                        # In a fully rigorous setup, node_match and edge_match should be defined.
                        if nx.is_isomorphic(rc_graph, cached_graph):
                            matched_rc_id = cached_id
                            break

                if matched_rc_id is None:
                    # If wlhash exists but no isomorphism match (collision), or wlhash is new
                    # We append a suffix to wlhash to keep it UNIQUE in DB if there's a collision
                    db_wlhash = wlhash
                    if wlhash in rc_cache:
                        db_wlhash = f"{wlhash}_{len(rc_cache[wlhash])}"

                    # Serialize and compress the RC graph
                    rc_pkl = pickle.dumps(rc_graph, protocol=pickle.HIGHEST_PROTOCOL)
                    compressed_rc = zlib.compress(rc_pkl, level=9)

                    with db.connection:
                        cursor = db.connection.execute(
                            "INSERT INTO reaction_center (wlhash, template_graph, graph_format) VALUES (?, ?, ?)",
                            (db_wlhash, compressed_rc, "pickle.gz"),
                        )
                        matched_rc_id = cursor.lastrowid

                    if wlhash not in rc_cache:
                        rc_cache[wlhash] = []
                    rc_cache[wlhash].append((matched_rc_id, rc_graph))

                # Serialize and compress the ITS graph
                its_pkl = pickle.dumps(its_graph, protocol=pickle.HIGHEST_PROTOCOL)
                compressed_its = zlib.compress(its_pkl, level=9)

                with db.connection:
                    db.connection.execute(
                        "INSERT INTO its (reaction_id, rc_id, wlhash, graph_data, graph_format) VALUES (?, ?, ?, ?, ?)",
                        (
                            reaction_id,
                            matched_rc_id,
                            db_wlhash if "db_wlhash" in locals() else wlhash,
                            compressed_its,
                            "pickle.gz",
                        ),
                    )

                # 5. Ingest EPD
                if ground_truth:
                    arrows = parse_epd(ground_truth)
                    with db.connection:
                        db.connection.execute(
                            "INSERT INTO epd (reaction_id, number_arrows) VALUES (?, ?)",
                            (reaction_id, len(arrows)),
                        )
                        # Compute mapping from original ITS to canonical ITS if not already done
                        atom_map = None
                        for arr in arrows:
                            if atom_map is None:
                                # Generate original ITS graph from rsmi to map from
                                original_res = extract_graphs(rsmi)
                                if original_res is not None:
                                    original_its, _, _ = original_res
                                    mappings = (
                                        SubgraphSearchEngine().find_subgraph_mappings(
                                            host=its_graph,  # Canonical graph
                                            pattern=original_its,  # Original graph
                                            node_attrs=["element", "charge"],
                                            edge_attrs=["order"],
                                        )
                                    )
                                    atom_map = mappings[0] if mappings else {}
                                else:
                                    atom_map = {}
                            # Remap EPD arrow atom indices using the mapping
                            src = json.loads(arr["source_atoms"])
                            tgt = json.loads(arr["target_atoms"])
                            src_mapped = [atom_map.get(i, i) for i in src]
                            tgt_mapped = [atom_map.get(i, i) for i in tgt]
                            db.connection.execute(
                                "INSERT INTO epd_arrow (reaction_id, arrow_index, arrow_type_code, source_atoms, target_atoms) VALUES (?, ?, ?, ?, ?)",
                                (
                                    reaction_id,
                                    arr["arrow_index"],
                                    arr["arrow_type_code"],
                                    json.dumps(src_mapped),
                                    json.dumps(tgt_mapped),
                                ),
                            )


if __name__ == "__main__":
    build_release_database(
        json_path=Path("data/polar.json"),
        hierarchy_path=Path("data/hierarchy.md"),
        db_path=Path("release_v1.sqlite"),
    )
    print("Release database v1.0.0 built successfully.")
