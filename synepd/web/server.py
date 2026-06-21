import os
import json
import zlib
import pickle
from pathlib import Path
from typing import Any, Dict, List, Optional
import networkx as nx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from synepd.core.query import (
    _get_connection,
    _execute_query,
    _read_bytes,
    query_epd_by_reaction,
    extract_graphs,
)

app = FastAPI(
    title="SynEPD Mechanistic Web Service",
    description="REST backend and interactive explorer for reaction EPD mechanisms",
    version="1.0.0",
)

# Enable CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db_path_or_url() -> str:
    # Use environment var or fallback to local release_v1.sqlite
    return os.environ.get("SYNEPD_DATABASE_URL", "release_v1.sqlite")


def serialize_graph(graph: nx.Graph) -> dict:
    if graph is None:
        return {"nodes": [], "links": []}

    nodes_list = []
    for n, data in graph.nodes(data=True):
        # Extract attributes safely handling tuples from ITS
        element = data.get("element")
        if isinstance(element, (list, tuple)):
            element = element[0]

        charge = data.get("charge")
        if isinstance(charge, (list, tuple)):
            charge = charge[0]

        atom_map = data.get("atom_map")
        if isinstance(atom_map, (list, tuple)):
            atom_map = atom_map[0]
        if atom_map is None:
            atom_map = n

        nodes_list.append(
            {
                "id": int(n),
                "element": str(element),
                "charge": int(charge),
                "atom_map": int(atom_map),
            }
        )

    links_list = []
    for u, v, data in graph.edges(data=True):
        order = data.get("order")
        order_r = 1.0
        order_p = 1.0
        if isinstance(order, (list, tuple)):
            order_r = float(order[0])
            order_p = float(order[1])

        if order_r > 0 and order_p == 0:
            status = "breaking"
        elif order_r == 0 and order_p > 0:
            status = "forming"
        elif order_r > 0 and order_p > 0 and order_r != order_p:
            status = "changing"
        else:
            status = "unchanged"

        links_list.append(
            {
                "source": int(u),
                "target": int(v),
                "order_r": order_r,
                "order_p": order_p,
                "status": status,
            }
        )

    return {"nodes": nodes_list, "links": links_list}


def compute_rdkit_coords(aam_key: str) -> dict:
    if not aam_key:
        return {}
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem

        parts = aam_key.split(">>")
        reactants_smiles = parts[0]

        params = Chem.SmilesParserParams()
        params.removeHs = False
        mol = Chem.MolFromSmiles(reactants_smiles, params)
        if mol:
            AllChem.Compute2DCoords(mol)
            conf = mol.GetConformer()
            coords = {}
            for atom in mol.GetAtoms():
                map_num = atom.GetAtomMapNum()
                if map_num > 0:
                    pos = conf.GetAtomPosition(atom.GetIdx())
                    # Scale coordinates to fit nicely in the viewport
                    coords[int(map_num)] = {
                        "x": float(pos.x * 65),
                        "y": float(-pos.y * 65),
                    }
            return coords
    except Exception:
        pass
    return {}


@app.get("/api/db-info")
def get_db_info():
    db_path = get_db_path_or_url()
    try:
        conn, is_pg = _get_connection(db_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database connection failed: {e}")

    try:
        cur = conn.cursor()

        # Get count of reactions
        cur.execute("SELECT COUNT(*) FROM reaction;")
        reaction_count = cur.fetchone()[0]

        # Get count of molecules
        cur.execute("SELECT COUNT(*) FROM molecule;")
        molecule_count = cur.fetchone()[0]

        # Get count of taxons under POLAR
        cur.execute(
            "SELECT COUNT(*) FROM taxon WHERE code = 'POLAR' OR code LIKE 'POLAR.%';"
        )
        taxon_count = cur.fetchone()[0]

        # Get version metadata
        cur.execute(
            "SELECT version, release_date, license FROM dataset_release LIMIT 1;"
        )
        row = cur.fetchone()
        if row:
            db_version = row[0]
            db_release_date = row[1]
            db_license = row[2]
        else:
            db_version = "v1.0.0"
            db_release_date = "2026-06-21"
            db_license = "MIT"

        return {
            "version": db_version,
            "release_date": db_release_date,
            "license": db_license,
            "counts": {
                "reactions": reaction_count,
                "molecules": molecule_count,
                "taxons": taxon_count,
            },
            "backend": "PostgreSQL" if is_pg else "SQLite",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@app.get("/api/taxonomy")
def get_taxonomy():
    db_path = get_db_path_or_url()
    try:
        conn, is_pg = _get_connection(db_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database connection failed: {e}")

    try:
        cur = conn.cursor()
        # Filter taxonomy to show POLAR only
        cur.execute("""
            SELECT code, parent_code, level, name 
            FROM taxon 
            WHERE code = 'POLAR' OR code LIKE 'POLAR.%'
            ORDER BY level, code;
        """)
        cols = [desc[0] for desc in cur.description]
        taxons = [dict(zip(cols, row)) for row in cur.fetchall()]

        cur.execute("""
            SELECT rt.taxon_code, r.id, r.case_id, r.canonical_rsmi, r.name
            FROM reaction_taxonomy rt
            JOIN reaction r ON r.id = rt.reaction_id
            WHERE rt.taxon_code = 'POLAR' OR rt.taxon_code LIKE 'POLAR.%'
        """)
        cols_rxn = [desc[0] for desc in cur.description]
        reactions = [dict(zip(cols_rxn, row)) for row in cur.fetchall()]

        # Group reactions by taxon
        rxn_by_taxon = {}
        for rxn in reactions:
            tcode = rxn["taxon_code"]
            if tcode not in rxn_by_taxon:
                rxn_by_taxon[tcode] = []
            rxn_by_taxon[tcode].append(
                {
                    "id": rxn["id"],
                    "case_id": rxn["case_id"],
                    "canonical_rsmi": rxn["canonical_rsmi"],
                    "name": rxn["name"],
                }
            )

        # Build nested tree
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
    finally:
        conn.close()


@app.get("/api/reactions/search")
def search_reactions(
    query: str = Query(
        ..., description="Query string to search for class name, Case ID, SMILES, etc."
    )
):
    db_path = get_db_path_or_url()
    try:
        conn, is_pg = _get_connection(db_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database connection failed: {e}")

    try:
        cur = conn.cursor()
        query_cleaned = query.strip()

        # Strip atom map numbers (e.g. :1, :2) to support searching unmapped/canonical SMILES
        import re

        query_stripped = re.sub(r":\d+", "", query_cleaned)

        # Check if case_id format
        rows = []
        if query_cleaned.upper().startswith("POLAR"):
            sql = "SELECT id, case_id, canonical_rsmi, aam_key, name FROM reaction WHERE UPPER(case_id) = ?"
            cur = _execute_query(conn, is_pg, sql, (query_cleaned.upper(),))
            rows = cur.fetchall()
            if not rows:
                sql_prefix = "SELECT id, case_id, canonical_rsmi, aam_key, name FROM reaction WHERE UPPER(case_id) LIKE ?"
                cur = _execute_query(
                    conn, is_pg, sql_prefix, (f"%{query_cleaned.upper()}%",)
                )
                rows = cur.fetchall()

        if not rows:
            # Check if query matches taxonomy names (fuzzy search) or code
            sql_tax = """
                SELECT r.id, r.case_id, r.canonical_rsmi, r.aam_key, r.name
                FROM reaction r
                JOIN reaction_taxonomy rt ON rt.reaction_id = r.id
                JOIN taxon t ON t.code = rt.taxon_code
                WHERE t.name LIKE ? OR t.code LIKE ? OR r.case_id LIKE ?
            """
            cur = _execute_query(
                conn,
                is_pg,
                sql_tax,
                (f"%{query_cleaned}%", f"%{query_cleaned}%", f"%{query_cleaned}%"),
            )
            rows = cur.fetchall()

        # If no results, try matching raw reactions table by smiles or canonical rsmi
        if not rows:
            sql_rxn = "SELECT id, case_id, canonical_rsmi, aam_key, name FROM reaction WHERE canonical_rsmi LIKE ? OR aam_key LIKE ?"
            cur = _execute_query(
                conn, is_pg, sql_rxn, (f"%{query_stripped}%", f"%{query_stripped}%")
            )
            rows = cur.fetchall()

        # If still no results, search via molecules side matches
        if not rows:
            sql_mol = """
                SELECT DISTINCT r.id, r.case_id, r.canonical_rsmi, r.aam_key, r.name
                FROM reaction r
                JOIN reaction_component rc ON rc.reaction_id = r.id
                JOIN molecule m ON m.id = rc.molecule_id
                WHERE m.canonical_smiles LIKE ?
            """
            cur = _execute_query(conn, is_pg, sql_mol, (f"%{query_stripped}%",))
            rows = cur.fetchall()

        results = []
        for r in rows:
            rxn_id = r[0]
            cur_tax = conn.cursor()
            sql_tax_code = (
                "SELECT taxon_code FROM reaction_taxonomy WHERE reaction_id = ?"
            )
            cur_tax = _execute_query(conn, is_pg, sql_tax_code, (rxn_id,))
            tax_row = cur_tax.fetchone()
            taxon_code = tax_row[0] if tax_row else None

            results.append(
                {
                    "id": rxn_id,
                    "case_id": r[1],
                    "canonical_rsmi": r[2],
                    "aam_key": r[3],
                    "name": r[4],
                    "taxonomy": taxon_code,
                }
            )
        return results
    finally:
        conn.close()


@app.get("/api/reactions/{reaction_id}")
def get_reaction_detail(reaction_id: int):
    db_path = get_db_path_or_url()
    try:
        conn, is_pg = _get_connection(db_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database connection failed: {e}")

    try:
        cur = conn.cursor()
        # Fetch reaction
        sql_rxn = "SELECT id, case_id, canonical_rsmi, aam_key, name FROM reaction WHERE id = ?"
        cur = _execute_query(conn, is_pg, sql_rxn, (reaction_id,))
        rxn_row = cur.fetchone()
        if not rxn_row:
            raise HTTPException(status_code=404, detail="Reaction not found")

        rxn_data = {
            "id": rxn_row[0],
            "case_id": rxn_row[1],
            "canonical_rsmi": rxn_row[2],
            "aam_key": rxn_row[3],
            "name": rxn_row[4],
        }

        # Fetch taxonomy
        sql_tax = """
            SELECT t.code, t.name, t.level
            FROM reaction_taxonomy rt
            JOIN taxon t ON t.code = rt.taxon_code
            WHERE rt.reaction_id = ?
        """
        cur = _execute_query(conn, is_pg, sql_tax, (reaction_id,))
        tax_row = cur.fetchone()
        rxn_data["taxonomy"] = (
            {"code": tax_row[0], "name": tax_row[1], "level": tax_row[2]}
            if tax_row
            else None
        )

        # Fetch EPD arrows
        sql_arr = "SELECT arrow_index, arrow_type_code, source_atoms, target_atoms FROM epd_arrow WHERE reaction_id = ? ORDER BY arrow_index;"
        cur = _execute_query(conn, is_pg, sql_arr, (reaction_id,))
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
        rxn_data["arrows"] = arrows

        # Fetch ITS graph
        sql_its = "SELECT graph_data, graph_format FROM its WHERE reaction_id = ?"
        cur = _execute_query(conn, is_pg, sql_its, (reaction_id,))
        its_row = cur.fetchone()
        its_json = {"nodes": [], "links": []}
        if its_row:
            graph_data, graph_format = its_row
            try:
                raw_bytes = _read_bytes(graph_data)
                its_graph = pickle.loads(zlib.decompress(raw_bytes))
                its_json = serialize_graph(its_graph)
            except Exception as ex:
                rxn_data["graph_error"] = str(ex)

        rxn_data["its_graph"] = its_json
        rxn_data["rdkit_coords"] = compute_rdkit_coords(rxn_data["aam_key"])

        return rxn_data
    finally:
        conn.close()


class EPDQueryRequest(BaseModel):
    rsmi: str


@app.post("/api/query-epd")
def query_epd(req: EPDQueryRequest):
    db_path = get_db_path_or_url()
    try:
        # Run standard EPD projection / query search logic
        res = query_epd_by_reaction(req.rsmi, db_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query failed: {e}")

    if not res.get("success"):
        return res

    # Standardize mapped_rsmi to produce canonical_rsmi
    if "canonical_rsmi" not in res and res.get("mapped_rsmi"):
        try:
            from synkit.Chem.Reaction.standardize import Standardize

            res["canonical_rsmi"] = Standardize().fit(res.get("mapped_rsmi"))
        except Exception:
            import re

            res["canonical_rsmi"] = re.sub(r":\d+", "", res.get("mapped_rsmi"))

    # Enrich response with ITS graph
    # If path 1 (reaction was in DB), load ITS directly
    rxn_id = res.get("reaction_id")
    its_json = {"nodes": [], "links": []}
    if rxn_id:
        try:
            conn, is_pg = _get_connection(db_path)
            cur = conn.cursor()
            sql_its = "SELECT graph_data FROM its WHERE reaction_id = ?"
            cur = _execute_query(conn, is_pg, sql_its, (rxn_id,))
            its_row = cur.fetchone()
            if its_row:
                raw_bytes = _read_bytes(its_row[0])
                its_graph = pickle.loads(zlib.decompress(raw_bytes))
                its_json = serialize_graph(its_graph)

            # Fetch taxonomy
            sql_tax = """
                SELECT t.code, t.name, t.level
                FROM reaction_taxonomy rt
                JOIN taxon t ON t.code = rt.taxon_code
                WHERE rt.reaction_id = ?
            """
            cur = _execute_query(conn, is_pg, sql_tax, (rxn_id,))
            tax_row = cur.fetchone()
            if tax_row:
                res["taxonomy"] = {
                    "code": tax_row[0],
                    "name": tax_row[1],
                    "level": tax_row[2],
                }
            conn.close()
        except Exception:
            pass
    else:
        # Path 2 (projected reaction), extract its graph from the matched/mapped rsmi
        mapped_rsmi = res.get("mapped_rsmi")
        if mapped_rsmi:
            try:
                extracted = extract_graphs(mapped_rsmi)
                if extracted:
                    its_graph, _, _ = extracted
                    its_json = serialize_graph(its_graph)
            except Exception:
                pass

    res["its_graph"] = its_json
    res["rdkit_coords"] = compute_rdkit_coords(res.get("mapped_rsmi"))
    return res


# Fallback/Default redirect to index.html
@app.get("/", response_class=HTMLResponse)
def get_index():
    static_file = Path(__file__).parent / "static" / "index.html"
    if static_file.exists():
        with open(static_file, "r", encoding="utf-8") as f:
            return f.read()
    return "<h3>SynEPD Web Explorer: static/index.html not found.</h3>"


# Mount static files directory if it exists
static_path = Path(__file__).parent / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=static_path), name="static")
