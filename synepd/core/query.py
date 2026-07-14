import json
import sqlite3
import zlib
import pickle
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import networkx as nx
from rdkit import Chem
from synkit.Chem.Reaction.standardize import Standardize
from synkit.Synthesis.Reactor.syn_reactor import SynReactor
from synkit.IO import rsmi_to_its
from synkit.Graph.Matcher.subgraph_matcher import SubgraphSearchEngine
from synepd.core.ingest import extract_graphs


def _resolve_query_db_path(
    db_path: Optional[Union[str, Path]],
    db_source: Optional[str],
    db_version: Optional[str],
) -> Union[str, Path]:
    if db_path is not None:
        return db_path
    if db_source is not None or db_version is not None:
        from synepd.core.data import get_default_db_path

        return get_default_db_path(version=db_version, source=db_source or "zenodo")
    return "data/epdb.sqlite"


def _get_connection(db_path_or_url: Union[str, Path]):
    # Get from environment if set, otherwise use db_path_or_url
    db_str = os.environ.get("SYNEPD_DATABASE_URL", str(db_path_or_url))
    is_pg = (
        db_str.startswith("postgresql://")
        or db_str.startswith("postgres://")
        or db_str.startswith("host=")
        or "user=" in db_str
    )
    if is_pg:
        import psycopg2

        conn = psycopg2.connect(db_str)
        return conn, True
    else:
        # If it points to an environment placeholder but we have local file fallback
        if db_str.startswith("postgresql://") or db_str.startswith("postgres://"):
            from synepd.core.data import get_default_db_path

            db_path = get_default_db_path()
        else:
            db_path = Path(db_str)
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn, False


def _execute_query(conn, is_pg: bool, sql: str, params: tuple = ()):
    cur = conn.cursor()
    if is_pg:
        sql = sql.replace("?", "%s")
    cur.execute(sql, params)
    return cur


def _read_bytes(val) -> bytes:
    if val is None:
        return b""
    if isinstance(val, memoryview):
        return val.tobytes()
    return bytes(val)


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


def standardize_side(side_smiles: str) -> str:
    parts = side_smiles.split(".")
    flat_parts = [strip_atom_map(p) for p in parts]
    flat_parts.sort()
    return ".".join(flat_parts)


def find_reactions_by_template(
    template: Union[str, nx.Graph],
    db_path: Optional[Union[str, Path]] = None,
    db_source: Optional[str] = None,
    db_version: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Scenario 1: Input a template (either mapped reaction SMILES or reaction center graph),
    and find all reactions in the database that match this template.
    """
    db_conn_str = _resolve_query_db_path(db_path, db_source, db_version)
    conn, is_pg = _get_connection(db_conn_str)

    # Determine rc_graph and WL hash from the template
    if isinstance(template, str):
        res = extract_graphs(template)
        if res is None:
            conn.close()
            return []
        _, rc_graph, _ = res
    elif isinstance(template, nx.Graph):
        rc_graph = template
    else:
        conn.close()
        raise TypeError(
            "template must be either a string (mapped reaction SMILES) or a NetworkX Graph"
        )

    from synkit.Graph.Feature.wl_hash import WLHash

    wlhash = WLHash(iterations=3).weisfeiler_lehman_graph_hash(rc_graph)

    cur = _execute_query(
        conn,
        is_pg,
        "SELECT id, wlhash, template_graph FROM reaction_center WHERE wlhash LIKE ?",
        (f"{wlhash}%",),
    )
    rc_candidates = cur.fetchall()

    rc_id = None
    for candidate_id, db_wlhash, template_bytes in rc_candidates:
        try:
            rc_pkl = zlib.decompress(_read_bytes(template_bytes))
            db_rc_graph = pickle.loads(rc_pkl)
            if nx.is_isomorphic(rc_graph, db_rc_graph):
                rc_id = candidate_id
                break
        except Exception:
            continue

    if rc_id is None:
        conn.close()
        return []

    # Get all reaction_ids from its table matching this rc_id
    cur = _execute_query(
        conn, is_pg, "SELECT reaction_id FROM its WHERE rc_id = ?", (rc_id,)
    )
    reaction_ids = [row[0] for row in cur.fetchall()]

    if not reaction_ids:
        conn.close()
        return []

    # Retrieve all matched reactions from the reaction table
    placeholders = ",".join("?" for _ in reaction_ids)
    cur = _execute_query(
        conn,
        is_pg,
        f"SELECT id, case_id, canonical_rsmi, aam_key FROM reaction WHERE id IN ({placeholders})",
        tuple(reaction_ids),
    )
    reactions = []
    for row in cur.fetchall():
        reactions.append(
            {
                "reaction_id": row[0],
                "case_id": row[1],
                "canonical_rsmi": row[2],
                "aam_key": row[3],
            }
        )

    conn.close()
    return reactions


def query_epd_by_reaction(
    rsmi: str,
    db_path: Optional[Union[str, Path]] = None,
    db_source: Optional[str] = None,
    db_version: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Scenario 2: Query EPD arrows for a given reaction SMILES.
    Supports both mapped and unmapped reactions.
    Projects template arrows if the exact reaction is not in the database, but a matching reaction center template is.
    """
    db_conn_str = _resolve_query_db_path(db_path, db_source, db_version)
    conn, is_pg = _get_connection(db_conn_str)

    # Standardize the query rsmi to check for direct matches
    try:
        canonical_rsmi = Standardize().fit(rsmi)
    except Exception as e:
        conn.close()
        return {"success": False, "error": f"Failed to standardize reaction: {e}"}

    # Path 1: Check if reaction exists in reaction table
    cur = _execute_query(
        conn,
        is_pg,
        "SELECT id, case_id, canonical_rsmi, aam_key, name FROM reaction WHERE canonical_rsmi = ?",
        (canonical_rsmi,),
    )
    row = cur.fetchone()
    if row:
        rxn_id, case_id, db_canonical_rsmi, aam_key, name = row
        cur = _execute_query(
            conn,
            is_pg,
            "SELECT arrow_index, arrow_type_code, source_atoms, target_atoms FROM epd_arrow WHERE reaction_id = ?",
            (rxn_id,),
        )
        arrows = []
        for idx, code, src_json, tgt_json in cur.fetchall():
            arrows.append(
                {
                    "arrow_index": idx,
                    "arrow_type_code": code,
                    "source_atoms": json.loads(src_json),
                    "target_atoms": json.loads(tgt_json),
                }
            )
        conn.close()
        return {
            "success": True,
            "path": 1,
            "reaction_id": rxn_id,
            "case_id": case_id,
            "canonical_rsmi": db_canonical_rsmi,
            "mapped_rsmi": aam_key,
            "name": name,
            "arrows": arrows,
        }

    # Path 2: Reaction does not exist in reaction table. Check templates.
    parts = rsmi.split(">>")
    if len(parts) != 2:
        conn.close()
        return {"success": False, "error": "Invalid reaction SMILES structure"}

    r_query, p_query = parts
    # Check if the query is already atom-mapped
    is_already_mapped = any(f":{i}" in rsmi for i in range(1, 100))

    matched_rc = None
    matched_smart = None
    matched_rc_id = None
    matched_wlhash = None
    new_its = None

    if is_already_mapped:
        # Extract rc and its graphs directly from the mapped input
        res = extract_graphs(rsmi)
        if res is not None:
            its_graph, rc_graph, wlhash = res
            # Check if this rc is in the DB
            cur = _execute_query(
                conn,
                is_pg,
                "SELECT id, wlhash, template_graph FROM reaction_center WHERE wlhash LIKE ?",
                (f"{wlhash}%",),
            )
            for rc_id, db_wlhash, template_bytes in cur.fetchall():
                try:
                    rc_pkl = zlib.decompress(_read_bytes(template_bytes))
                    db_rc_graph = pickle.loads(rc_pkl)
                    if nx.is_isomorphic(rc_graph, db_rc_graph):
                        matched_rc = db_rc_graph
                        matched_smart = rsmi
                        matched_rc_id = rc_id
                        matched_wlhash = db_wlhash
                        new_its = its_graph
                        break
                except Exception:
                    continue

    # If not mapped, or if mapped extraction didn't find matching rc in DB, do reactor/balance search
    if matched_rc is None:
        from synepd.precheck.check_balance import check_reaction_balance

        is_balanced = False
        try:
            is_balanced = check_reaction_balance(rsmi).balanced
        except Exception:
            pass

        cur = _execute_query(
            conn, is_pg, "SELECT id, wlhash, template_graph FROM reaction_center"
        )
        rc_rows = cur.fetchall()

        if is_balanced:
            p_std = standardize_side(p_query)

            for rc_id, wlhash, template_bytes in rc_rows:
                try:
                    rc_pkl = zlib.decompress(_read_bytes(template_bytes))
                    rc_graph = pickle.loads(rc_pkl)
                except Exception:
                    continue

                try:
                    reactor = SynReactor(
                        substrate=r_query,
                        template=rc_graph,
                        explicit_h=True,
                        implicit_temp=False,
                    )
                    smarts_list = reactor.smarts
                except Exception:
                    continue

                if smarts_list:
                    for smart in smarts_list:
                        smart_parts = smart.split(">>")
                        if len(smart_parts) == 2:
                            p_smart_std = standardize_side(smart_parts[1])
                            if p_smart_std == p_std:
                                matched_rc = rc_graph
                                matched_smart = smart
                                matched_rc_id = rc_id
                                matched_wlhash = wlhash
                                break
                    if matched_rc:
                        break
        else:
            # Imbalanced reaction case: use RBLEngine
            from synkit.Synthesis.Reactor.rbl_engine import RBLEngine

            engine = RBLEngine(
                early_stop=True,
                fast_paths_only=False,
                implicit_temp=False,
                explicit_h=True,
                embed_threshold=5000,
            )

            def flatten(lst):
                res_list = []
                for item in lst:
                    if isinstance(item, list):
                        res_list.extend(flatten(item))
                    else:
                        res_list.append(item)
                return res_list

            for rc_id, wlhash, template_bytes in rc_rows:
                try:
                    rc_pkl = zlib.decompress(_read_bytes(template_bytes))
                    rc_graph = pickle.loads(rc_pkl)
                except Exception:
                    continue

                try:
                    res_engine = engine.process(rsmi, rc_graph)
                    flat_fused = flatten(res_engine.fused_rsmis)
                except Exception:
                    continue

                if flat_fused:
                    balanced_mapped = flat_fused[0]
                    # Strip atom mapping numbers to get balanced unmapped reaction
                    reactants_part, products_part = balanced_mapped.split(">>")
                    reactants_flat = ".".join(
                        strip_atom_map(frag) for frag in reactants_part.split(".")
                    )
                    products_flat = ".".join(
                        strip_atom_map(frag) for frag in products_part.split(".")
                    )
                    balanced_unmapped = f"{reactants_flat}>>{products_flat}"

                    conn.close()
                    # Recursively query the balanced reaction
                    recursive_res = query_epd_by_reaction(
                        balanced_unmapped, db_path=db_conn_str
                    )
                    if recursive_res.get("success"):
                        recursive_res["balanced_from_imbalanced"] = True
                        recursive_res["original_imbalanced_query"] = rsmi
                        return recursive_res

            # If no template balanced the reaction, fail
            conn.close()
            return {
                "success": False,
                "error": "Could not balance the reaction with any database template",
            }

        if matched_rc is not None:
            try:
                new_its = rsmi_to_its(matched_smart, core=False, format="tuple")
            except Exception:
                matched_rc = None

    if matched_rc is None or new_its is None:
        conn.close()
        return {
            "success": False,
            "error": "No matching reaction center found in database",
        }

    # Find mapping from RC template graph to new ITS graph
    mappings = SubgraphSearchEngine().find_subgraph_mappings(
        host=new_its,
        pattern=matched_rc,
        node_attrs=["element", "charge"],
        edge_attrs=["order"],
    )

    if not mappings:
        conn.close()
        return {
            "success": False,
            "error": "Could not map reaction center template to query reaction ITS",
        }

    atom_map = mappings[0]

    # Find a reference reaction ID sharing this rc_id to fetch template EPD arrows
    cur = _execute_query(
        conn,
        is_pg,
        "SELECT reaction_id FROM its WHERE rc_id = ? LIMIT 1",
        (matched_rc_id,),
    )
    ref_row = cur.fetchone()
    if not ref_row:
        conn.close()
        return {
            "success": False,
            "error": "Could not find reference reaction sharing this reaction center",
        }

    ref_reaction_id = ref_row[0]

    # Get the mapping from the reference reaction to the RC template
    cur = _execute_query(
        conn,
        is_pg,
        "SELECT case_id, name, aam_key FROM reaction WHERE id = ?",
        (ref_reaction_id,),
    )
    ref_case_id, ref_name, ref_aam_key = cur.fetchone()
    ref_its, _, _ = extract_graphs(ref_aam_key)

    ref_mappings = SubgraphSearchEngine().find_subgraph_mappings(
        host=ref_its,
        pattern=matched_rc,
        node_attrs=["element", "charge"],
        edge_attrs=["order"],
    )

    if not ref_mappings:
        conn.close()
        return {
            "success": False,
            "error": "Could not map reference reaction to template RC",
        }

    ref_to_rc = {v: k for k, v in ref_mappings[0].items()}

    # Query template EPD arrows
    cur = _execute_query(
        conn,
        is_pg,
        "SELECT arrow_index, arrow_type_code, source_atoms, target_atoms FROM epd_arrow WHERE reaction_id = ?",
        (ref_reaction_id,),
    )
    ref_arrows = cur.fetchall()

    projected_arrows = []
    for idx, code, src_json, tgt_json in ref_arrows:
        src = json.loads(src_json)
        tgt = json.loads(tgt_json)

        # Project source atoms: ref_its -> rc -> new_its
        src_mapped = []
        for s in src:
            rc_atom = ref_to_rc.get(s)
            if rc_atom is not None:
                new_atom = atom_map.get(rc_atom)
                if new_atom is not None:
                    src_mapped.append(new_atom)

        # Project target atoms: ref_its -> rc -> new_its
        tgt_mapped = []
        for t in tgt:
            rc_atom = ref_to_rc.get(t)
            if rc_atom is not None:
                new_atom = atom_map.get(rc_atom)
                if new_atom is not None:
                    tgt_mapped.append(new_atom)

        projected_arrows.append(
            {
                "arrow_index": idx,
                "arrow_type_code": code,
                "source_atoms": src_mapped,
                "target_atoms": tgt_mapped,
            }
        )

    conn.close()
    return {
        "success": True,
        "path": 2,
        "reference_reaction_id": ref_reaction_id,
        "reference_case_id": ref_case_id,
        "name": ref_name,
        "reaction_center_id": matched_rc_id,
        "reaction_center_wlhash": matched_wlhash,
        "mapped_rsmi": matched_smart,
        "arrows": projected_arrows,
    }
