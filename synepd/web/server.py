import os
import json
import logging
import zlib
import pickle
import time
import uuid
import sqlite3 as _sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import networkx as nx
from fastapi import FastAPI, HTTPException, Query, Request, Response, Header
from fastapi.responses import HTMLResponse, JSONResponse
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

# --- Knowledge Graph router ---
from synepd.web.knowledge_graph import router as kg_router

_CACHE_TTL = 300  # 5 minutes


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.environ.get("SYNEPD_BUILD_DOCS") == "1":
        import threading

        threading.Thread(target=build_sphinx_docs, daemon=True).start()
    try:
        from synepd.web.knowledge_graph import _ensure_rfp_matrix, _ensure_rxn_graph

        _ensure_rfp_matrix()
        _ensure_rxn_graph()
    except Exception:
        pass
    yield


app = FastAPI(
    title="SynEPD Mechanistic Web Service",
    description="REST backend and interactive explorer for reaction EPD mechanisms",
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS
origins = os.environ.get("SYNEPD_CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


app.include_router(kg_router)


def build_sphinx_docs():
    import subprocess

    try:
        project_root = Path(__file__).parent.parent.parent
        docs_dir = project_root / "docs"
        if docs_dir.exists():
            subprocess.run(
                ["make", "html"],
                cwd=str(docs_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception:
        pass


def get_db_path_or_url() -> str:
    return os.environ.get("SYNEPD_DATABASE_URL", "data/epdb.sqlite")


def db_cache_token(db_path_or_url: str) -> str:
    """Return a stable token that changes when a local SQLite file is rebuilt."""
    if db_path_or_url.startswith(("postgresql://", "postgres://", "host=")):
        return db_path_or_url
    try:
        path = Path(db_path_or_url)
        stat = path.stat()
        return f"{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        return db_path_or_url


def get_submissions_db_path() -> str:
    return os.environ.get("SYNEPD_SUBMISSIONS_PATH", "submissions_cache.sqlite")


def _get_submissions_conn():
    """Open the writable local submissions cache, separate from release DB."""
    conn = _sqlite3.connect(get_submissions_db_path())
    conn.row_factory = _sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL CHECK(type IN ('reaction', 'issue')),
            label TEXT,
            rsmi TEXT,
            epd_lw TEXT,
            note TEXT,
            submitted_at TEXT NOT NULL,
            user_agent TEXT,
            status TEXT NOT NULL DEFAULT 'pending'
        );
        """)
    conn.commit()
    return conn


def serialize_graph(graph: nx.Graph) -> dict:
    if graph is None:
        return {"nodes": [], "links": []}

    def _as_int(v, default=0):
        if isinstance(v, (list, tuple)):
            v = v[0] if v else default
        try:
            return int(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    nodes_list = []
    for n, data in graph.nodes(data=True):
        # Extract attributes safely handling tuples from ITS
        element = data.get("element")
        if isinstance(element, (list, tuple)):
            element = element[0]

        charge = data.get("charge")
        atom_map = data.get("atom_map")
        if atom_map is None:
            atom_map = n

        hybrid = data.get("hybridization")
        if isinstance(hybrid, (list, tuple)):
            hybrid = hybrid[0]

        aromatic = data.get("aromatic")
        if isinstance(aromatic, (list, tuple)):
            aromatic = bool(aromatic[0])

        nodes_list.append(
            {
                "id": _as_int(n),
                "element": str(element) if element is not None else "",
                "charge": _as_int(charge, 0),
                "atom_map": _as_int(atom_map, _as_int(n)),
                "hybridization": str(hybrid) if hybrid is not None else None,
                "aromatic": bool(aromatic) if aromatic is not None else False,
            }
        )

    links_list = []
    for u, v, data in graph.edges(data=True):
        # Use Kekulé order (integers) when available; fall back to fractional order
        kekule = data.get("kekule_order")
        raw_order = data.get("order")
        ref = kekule if kekule is not None else raw_order
        order_r = 0.0
        order_p = 0.0
        if isinstance(ref, (list, tuple)) and len(ref) >= 2:
            order_r = float(ref[0])
            order_p = float(ref[1])
        elif isinstance(raw_order, (list, tuple)) and len(raw_order) >= 2:
            order_r = float(raw_order[0])
            order_p = float(raw_order[1])

        # Bond order decrease (2→1, 1→0) → breaking; increase (1→2, 0→1) → forming
        if order_r > order_p:
            status = "breaking"
        elif order_r < order_p:
            status = "forming"
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


def _deserialize_graph(raw: bytes) -> nx.Graph:
    decompressed = zlib.decompress(raw)
    try:
        data = json.loads(decompressed)
        return nx.node_link_graph(data)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return pickle.loads(decompressed)  # legacy fallback


def _primary_taxon_sql(reaction_alias: str = "r") -> str:
    return (
        f"(SELECT MIN(rt_primary.taxon_code) "
        f"FROM reaction_taxonomy rt_primary "
        f"WHERE rt_primary.reaction_id = {reaction_alias}.id)"
    )


def compute_rdkit_coords(aam_key: str) -> dict:
    if not aam_key:
        return {}
    coords = {}
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem

        params = Chem.SmilesParserParams()
        params.removeHs = False

        sides = aam_key.split(">>")
        if len(sides) == 2:
            # Anchor Chemist 2D on the product depiction; this keeps product
            # geometry readable while reactant-only atoms still get coordinates.
            sides = [sides[1], sides[0]]

        for side_smiles in sides:
            try:
                mol = Chem.MolFromSmiles(side_smiles, params)
                if mol:
                    AllChem.Compute2DCoords(mol)
                    conf = mol.GetConformer()
                    for atom in mol.GetAtoms():
                        map_num = atom.GetAtomMapNum()
                        if map_num > 0 and map_num not in coords:
                            pos = conf.GetAtomPosition(atom.GetIdx())
                            coords[int(map_num)] = {
                                "x": float(pos.x * 65),
                                "y": float(-pos.y * 65),
                            }
            except Exception:
                pass
    except Exception:
        pass
    return coords


def _check_balanced(canonical_rsmi: str) -> bool | None:
    """Return True if atom counts balance across the reaction arrow, False if not,
    or None if the RSMI is missing or unparseable."""
    if not canonical_rsmi:
        return None
    try:
        from rdkit import Chem
        from collections import Counter

        parts = canonical_rsmi.split(">>")
        # Support both "R>>P" (2-part) and "R>>reagent>>P" (3-part); ignore middle.
        if len(parts) == 2:
            lhs, rhs = parts[0], parts[1]
        elif len(parts) == 3:
            lhs, rhs = parts[0], parts[2]
        else:
            return None

        def atom_counts(frag: str) -> Counter | None:
            counts: Counter = Counter()
            for smi in frag.split("."):
                mol = Chem.MolFromSmiles(smi)
                if mol is None:
                    return None
                mol = Chem.AddHs(mol)
                for a in mol.GetAtoms():
                    counts[a.GetAtomicNum()] += 1
            return counts

        r_counts = atom_counts(lhs)
        p_counts = atom_counts(rhs)
        if r_counts is None or p_counts is None:
            return None
        return r_counts == p_counts
    except Exception:
        return None


def _render_rdkit_svg(smi: str, kind: str = "auto") -> str:
    """Render a molecule or reaction SMILES to SVG using local RDKit."""
    smi = smi.strip()
    if not smi:
        raise ValueError("SMILES is required")
    if len(smi) > 5000:
        raise ValueError("SMILES is too long")

    kind = kind.lower()
    if kind not in {"auto", "molecule", "reaction"}:
        raise ValueError("kind must be auto, molecule, or reaction")

    is_reaction = kind == "reaction" or (kind == "auto" and ">" in smi)

    from rdkit import Chem
    from rdkit.Chem import rdChemReactions
    from rdkit.Chem.Draw import rdMolDraw2D

    if is_reaction:
        rxn = rdChemReactions.ReactionFromSmarts(smi, useSmiles=True)
        if rxn is None:
            raise ValueError("Invalid reaction SMILES")
        drawer = rdMolDraw2D.MolDraw2DSVG(760, 240)
        drawer.DrawReaction(rxn)
    else:
        mol = Chem.MolFromSmiles(smi)
        if mol is None:
            raise ValueError("Invalid molecule SMILES")
        drawer = rdMolDraw2D.MolDraw2DSVG(420, 260)
        rdMolDraw2D.PrepareAndDrawMolecule(drawer, mol)

    drawer.FinishDrawing()
    return drawer.GetDrawingText()


@app.get("/api/render/rdkit.svg")
def render_rdkit_svg(smi: str = Query(...), kind: str = "auto"):
    try:
        svg = _render_rdkit_svg(smi, kind=kind)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"RDKit rendering failed: {exc}")
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@lru_cache(maxsize=4)
def _get_db_info_cached(db_path: str, _db_token: str, _ttl_bucket: int) -> dict:
    conn, is_pg = _get_connection(db_path)
    try:
        cur = conn.cursor()

        # Get count of reactions
        cur.execute("SELECT COUNT(*) FROM reaction;")
        reaction_count = cur.fetchone()[0]

        # Get count of molecules
        cur.execute("SELECT COUNT(*) FROM molecule;")
        molecule_count = cur.fetchone()[0]

        # Count populated class-level taxons
        cur.execute("""
            SELECT COUNT(DISTINCT t.code)
            FROM taxon t
            JOIN reaction_taxonomy rt ON rt.taxon_code = t.code
            WHERE t.level = 4
              AND t.code != 'POLAR.99'
              AND t.code NOT LIKE 'POLAR.99.%';
            """)
        taxon_count = cur.fetchone()[0]

        try:
            cur.execute("SELECT COUNT(*) FROM reaction_center;")
            rc_count = cur.fetchone()[0]
        except Exception:
            rc_count = 0

        try:
            cur.execute("SELECT COUNT(*) FROM epd_arrow;")
            epd_arrow_count = cur.fetchone()[0]
        except Exception:
            epd_arrow_count = 0

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
            db_version = "v0.1.0"
            db_release_date = "2026-07-07"
            db_license = "CC BY 4.0"

        # Calculate database file modification time for SQLite
        import os
        from datetime import datetime, timezone

        last_update = None
        if not is_pg and os.path.exists(db_path):
            try:
                mtime = os.path.getmtime(db_path)
                last_update = datetime.fromtimestamp(mtime, timezone.utc).strftime(
                    "%Y-%m-%d %H:%M UTC"
                )
            except Exception:
                pass
        if not last_update:
            last_update = db_release_date

        return {
            "version": db_version,
            "release_date": db_release_date,
            "last_update": last_update,
            "license": db_license,
            "counts": {
                "reactions": reaction_count,
                "molecules": molecule_count,
                "taxons": taxon_count,
                "reaction_centers": rc_count,
                "epd_arrows": epd_arrow_count,
            },
            "backend": "PostgreSQL" if is_pg else "SQLite",
        }
    finally:
        conn.close()


@app.get("/api/db-info")
def get_db_info(response: Response = None):
    if response is not None:
        response.headers["Cache-Control"] = "public, max-age=60"
    db_path = get_db_path_or_url()
    bucket = int(time.time() / _CACHE_TTL)
    try:
        return _get_db_info_cached(db_path, db_cache_token(db_path), bucket)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@lru_cache(maxsize=4)
def _get_taxonomy_cached(db_path: str, _db_token: str, _ttl_bucket: int) -> dict:
    conn, is_pg = _get_connection(db_path)
    try:
        cur = conn.cursor()
        # Filter taxonomy to show POLAR only
        cur.execute("""
            SELECT code, parent_code, level, name 
            FROM taxon 
            WHERE (code = 'POLAR' OR code LIKE 'POLAR.%')
              AND code != 'POLAR.99'
              AND code NOT LIKE 'POLAR.99.%'
            ORDER BY level, code;
        """)
        cols = [desc[0] for desc in cur.description]
        taxons = [dict(zip(cols, row)) for row in cur.fetchall()]

        cur.execute("""
            SELECT rt.taxon_code, COUNT(DISTINCT rt.reaction_id) AS reaction_count
            FROM reaction_taxonomy rt
            WHERE (rt.taxon_code = 'POLAR' OR rt.taxon_code LIKE 'POLAR.%')
              AND rt.taxon_code != 'POLAR.99'
              AND rt.taxon_code NOT LIKE 'POLAR.99.%'
            GROUP BY rt.taxon_code
        """)
        rxn_counts = {row[0]: row[1] for row in cur.fetchall()}

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
                "reaction_count": rxn_counts.get(code, 0),
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


@app.get("/api/taxonomy")
def get_taxonomy(response: Response = None):
    if response is not None:
        response.headers["Cache-Control"] = "public, max-age=60"
    db_path = get_db_path_or_url()
    bucket = int(time.time() / _CACHE_TTL)
    return _get_taxonomy_cached(db_path, db_cache_token(db_path), bucket)


@app.get("/api/reactions/search")
def search_reactions(
    query: str,
    limit: int = 20,
    offset: int = 0,
):
    if limit < 1:
        limit = 20
    if limit > 100:
        limit = 100
    if offset < 0:
        offset = 0
    db_path = get_db_path_or_url()
    try:
        conn, is_pg = _get_connection(db_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database connection failed: {e}")

    try:
        cur = conn.cursor()
        query_cleaned = query.strip()
        import re

        query_stripped = re.sub(r":\d+", "", query_cleaned)

        # Check if reaction_fts virtual table exists and query is clean
        has_fts = False
        try:
            cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='reaction_fts';"
            )
            if cur.fetchone():
                # Test FTS matching query syntax
                fts_query_str = query_cleaned.replace('"', '""')
                cur.execute(
                    "SELECT 1 FROM reaction_fts WHERE reaction_fts MATCH ? LIMIT 1",
                    (f'"{fts_query_str}"*',),
                )
                cur.fetchone()
                has_fts = True
        except Exception:
            has_fts = False

        params = []
        select_parts = []

        if has_fts:
            fts_query_str = query_cleaned.replace('"', '""')
            select_parts.append("""
                SELECT r.id, 1 as priority
                FROM reaction_fts fts
                JOIN reaction r ON r.id = fts.rowid
                WHERE reaction_fts MATCH ?
            """)
            params.append(f'"{fts_query_str}"*')

        select_parts.append("""
            SELECT id, 2 as priority
            FROM reaction
            WHERE UPPER(case_id) = ? OR UPPER(case_id) LIKE ?
        """)
        params.extend([query_cleaned.upper(), f"%{query_cleaned.upper()}%"])

        select_parts.append("""
            SELECT r.id, 3 as priority
            FROM reaction r
            JOIN reaction_taxonomy rt ON rt.reaction_id = r.id
            JOIN taxon t ON t.code = rt.taxon_code
            WHERE t.name LIKE ? OR t.code LIKE ? OR r.case_id LIKE ?
        """)
        params.extend(
            [f"%{query_cleaned}%", f"%{query_cleaned}%", f"%{query_cleaned}%"]
        )

        select_parts.append("""
            SELECT id, 4 as priority
            FROM reaction
            WHERE canonical_rsmi LIKE ? OR aam_key LIKE ?
        """)
        params.extend([f"%{query_stripped}%", f"%{query_stripped}%"])

        select_parts.append("""
            SELECT DISTINCT r.id, 5 as priority
            FROM reaction r
            JOIN reaction_component rc ON rc.reaction_id = r.id
            JOIN molecule m ON m.id = rc.molecule_id
            WHERE m.canonical_smiles LIKE ?
        """)
        params.append(f"%{query_stripped}%")

        union_sql = " UNION ".join(select_parts)

        # Combined query to group by id, take min priority
        inner_sql = f"""
            SELECT id, MIN(priority) as pri
            FROM ({union_sql})
            GROUP BY id
        """

        # Count total
        count_sql = f"SELECT COUNT(*) FROM ({inner_sql}) AS sub"
        cur = _execute_query(conn, is_pg, count_sql, tuple(params))
        total = cur.fetchone()[0]

        # Paginated results
        primary_taxon = _primary_taxon_sql("r")
        paginated_sql = f"""
            SELECT r.id, r.case_id, r.canonical_rsmi, r.aam_key, r.name,
                   sub.pri, {primary_taxon} AS taxon_code
            FROM ({inner_sql}) AS sub
            JOIN reaction r ON r.id = sub.id
            ORDER BY sub.pri, r.case_id
            LIMIT ? OFFSET ?
        """
        cur = _execute_query(
            conn, is_pg, paginated_sql, tuple(params) + (limit, offset)
        )
        rows = cur.fetchall()

        results = []
        for r in rows:
            results.append(
                {
                    "id": r[0],
                    "case_id": r[1],
                    "canonical_rsmi": r[2],
                    "aam_key": r[3],
                    "name": r[4],
                    "taxonomy": r[6],
                }
            )

        return {"total": total, "offset": offset, "limit": limit, "results": results}
    finally:
        conn.close()


@app.get("/api/reactions/random")
def get_random_reaction():
    """Return a random reaction ID for discovery."""
    db_path = get_db_path_or_url()
    conn, is_pg = _get_connection(db_path)
    try:
        cur = _execute_query(
            conn,
            is_pg,
            "SELECT id FROM reaction ORDER BY RANDOM() LIMIT 1",
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="No reactions in database")
        return {"reaction_id": row[0]}
    finally:
        conn.close()


@app.get("/api/reactions/by-arrow-count")
def get_reactions_by_arrow_count(n: int = Query(...), limit: int = 20, offset: int = 0):
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    db_path = get_db_path_or_url()
    conn, is_pg = _get_connection(db_path)
    try:
        cur = conn.cursor()
        primary_taxon = _primary_taxon_sql("r")
        cur = _execute_query(
            conn, is_pg, "SELECT COUNT(*) FROM epd WHERE number_arrows = ?", (n,)
        )
        total = cur.fetchone()[0]

        sql = f"""
            SELECT r.id, r.case_id, r.canonical_rsmi, r.aam_key, r.name, {primary_taxon} AS taxon_code
            FROM epd e
            JOIN reaction r ON r.id = e.reaction_id
            WHERE e.number_arrows = ?
            ORDER BY r.case_id
            LIMIT ? OFFSET ?
        """
        cur = _execute_query(conn, is_pg, sql, (n, limit, offset))
        results = [
            {
                "id": row[0],
                "case_id": row[1],
                "canonical_rsmi": row[2],
                "aam_key": row[3],
                "name": row[4],
                "taxonomy": row[5],
            }
            for row in cur.fetchall()
        ]
        return {"total": total, "offset": offset, "limit": limit, "results": results}
    finally:
        conn.close()


@app.get("/api/reactions/by-arrow-type")
def get_reactions_by_arrow_type(
    code: str = Query(...), mode: str = "contains", limit: int = 20, offset: int = 0
):
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    if mode not in ("contains", "excludes"):
        raise HTTPException(status_code=400, detail="mode must be contains or excludes")
    db_path = get_db_path_or_url()
    conn, is_pg = _get_connection(db_path)
    try:
        cur = conn.cursor()
        primary_taxon = _primary_taxon_sql("r")

        if mode == "contains":
            sub_sql = (
                "SELECT DISTINCT reaction_id FROM epd_arrow WHERE arrow_type_code = ?"
            )
        else:
            sub_sql = "SELECT id FROM reaction WHERE id NOT IN (SELECT reaction_id FROM epd_arrow WHERE arrow_type_code = ?)"

        cur = _execute_query(conn, is_pg, f"SELECT COUNT(*) FROM ({sub_sql})", (code,))
        total = cur.fetchone()[0]

        sql = f"""
            SELECT r.id, r.case_id, r.canonical_rsmi, r.aam_key, r.name, {primary_taxon} AS taxon_code
            FROM reaction r
            WHERE r.id IN ({sub_sql})
            ORDER BY r.case_id
            LIMIT ? OFFSET ?
        """
        cur = _execute_query(conn, is_pg, sql, (code, limit, offset))
        results = [
            {
                "id": row[0],
                "case_id": row[1],
                "canonical_rsmi": row[2],
                "aam_key": row[3],
                "name": row[4],
                "taxonomy": row[5],
            }
            for row in cur.fetchall()
        ]
        return {"total": total, "offset": offset, "limit": limit, "results": results}
    finally:
        conn.close()


@app.get("/api/reactions/by-signature")
def get_reactions_by_signature(
    pattern: str = Query(...),
    match_type: str = "subsequence",
    limit: int = 20,
    offset: int = 0,
):
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    if match_type not in ("exact", "prefix", "subsequence"):
        raise HTTPException(
            status_code=400, detail="match_type must be exact, prefix, or subsequence"
        )
    db_path = get_db_path_or_url()
    conn, is_pg = _get_connection(db_path)
    try:
        cur = conn.cursor()
        primary_taxon = _primary_taxon_sql("r")

        if match_type == "exact":
            sql_cond = "e.signature = ?"
            param = pattern
        elif match_type == "prefix":
            sql_cond = "e.signature LIKE ?"
            param = f"{pattern}%"
        else:
            import re

            parts = re.split(r"[,\s|]+", pattern.strip())
            sql_cond = "e.signature LIKE ?"
            param = "%" + "%".join(parts) + "%"

        cur = _execute_query(
            conn, is_pg, f"SELECT COUNT(*) FROM epd e WHERE {sql_cond}", (param,)
        )
        total = cur.fetchone()[0]

        sql = f"""
            SELECT r.id, r.case_id, r.canonical_rsmi, r.aam_key, r.name, {primary_taxon} AS taxon_code
            FROM epd e
            JOIN reaction r ON r.id = e.reaction_id
            WHERE {sql_cond}
            ORDER BY r.case_id
            LIMIT ? OFFSET ?
        """
        cur = _execute_query(conn, is_pg, sql, (param, limit, offset))
        results = [
            {
                "id": row[0],
                "case_id": row[1],
                "canonical_rsmi": row[2],
                "aam_key": row[3],
                "name": row[4],
                "taxonomy": row[5],
            }
            for row in cur.fetchall()
        ]
        return {"total": total, "offset": offset, "limit": limit, "results": results}
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
        conn.row_factory = _sqlite3.Row
        cur = conn.cursor()
        # Fetch reaction (try fetching upgraded columns first, fallback to legacy if not present)
        sql_rxn = "SELECT id, case_id, canonical_rsmi, aam_key, name, balanced, reactant_atom_count, product_atom_count, formal_charge_delta, coords_json FROM reaction WHERE id = ?"
        try:
            cur = _execute_query(conn, is_pg, sql_rxn, (reaction_id,))
            rxn_row = cur.fetchone()
            legacy = False
        except Exception:
            sql_rxn_legacy = "SELECT id, case_id, canonical_rsmi, aam_key, name FROM reaction WHERE id = ?"
            cur = _execute_query(conn, is_pg, sql_rxn_legacy, (reaction_id,))
            rxn_row = cur.fetchone()
            legacy = True

        if not rxn_row:
            raise HTTPException(status_code=404, detail="Reaction not found")

        rxn_data = {
            "id": rxn_row[0],
            "case_id": rxn_row[1],
            "canonical_rsmi": rxn_row[2],
            "aam_key": rxn_row[3],
            "name": rxn_row[4],
        }

        balanced_val = None
        reactant_atom_count = None
        product_atom_count = None
        formal_charge_delta = None
        coords_json = None

        if not legacy:
            balanced_val = rxn_row[5]
            reactant_atom_count = rxn_row[6]
            product_atom_count = rxn_row[7]
            formal_charge_delta = rxn_row[8]
            coords_json = rxn_row[9]

        rxn_data["reactant_atom_count"] = reactant_atom_count
        rxn_data["product_atom_count"] = product_atom_count
        rxn_data["formal_charge_delta"] = formal_charge_delta

        # Fetch taxonomy
        sql_tax = """
            SELECT t.code, t.name, t.level
            FROM reaction_taxonomy rt
            JOIN taxon t ON t.code = rt.taxon_code
            WHERE rt.reaction_id = ?
            ORDER BY t.code
        """
        cur = _execute_query(conn, is_pg, sql_tax, (reaction_id,))
        taxonomies = [
            {"code": row[0], "name": row[1], "level": row[2]} for row in cur.fetchall()
        ]
        rxn_data["taxonomies"] = taxonomies
        rxn_data["taxonomy"] = taxonomies[0] if taxonomies else None

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
                its_graph = _deserialize_graph(raw_bytes)
                its_json = serialize_graph(its_graph)
            except Exception as ex:
                rxn_data["graph_error"] = str(ex)

        rxn_data["its_graph"] = its_json

        coords_loaded = False
        if coords_json:
            try:
                coords_raw = json.loads(coords_json)
                rxn_data["rdkit_coords"] = {int(k): v for k, v in coords_raw.items()}
                coords_loaded = True
            except Exception:
                pass

        if not coords_loaded:
            rxn_data["rdkit_coords"] = compute_rdkit_coords(rxn_data["aam_key"])

        if balanced_val is not None:
            rxn_data["balanced"] = bool(balanced_val)
        else:
            rxn_data["balanced"] = _check_balanced(rxn_data.get("canonical_rsmi"))

        return rxn_data
    finally:
        conn.close()


@app.get("/api/reactions/{reaction_id}/export")
def export_reaction_json(reaction_id: int):
    detail = get_reaction_detail(reaction_id)
    taxonomy_code = detail["taxonomy"]["code"] if detail.get("taxonomy") else None
    export_data = {
        "case_id": detail["case_id"],
        "reaction_name": detail["name"],
        "canonical_smiles": detail["canonical_rsmi"],
        "atom_mapped_smiles": detail["aam_key"],
        "taxonomy_code": taxonomy_code,
        "epd_lw": [
            [
                arr["arrow_type_code"],
                arr["source_atoms"],
                arr["target_atoms"],
            ]
            for arr in detail["arrows"]
        ],
    }
    from fastapi.responses import JSONResponse

    return JSONResponse(
        content=export_data,
        headers={
            "Content-Disposition": f"attachment; filename={detail['case_id']}_epd.json"
        },
    )


class ExportBulkRequest(BaseModel):
    reaction_ids: List[int]
    template_ids: List[int]


@app.post("/api/reactions/export-bulk")
def export_reactions_bulk(req: ExportBulkRequest):
    conn, is_pg = _get_connection(get_db_path_or_url())
    try:
        cur = conn.cursor()
        all_reaction_ids = set(req.reaction_ids)

        if req.template_ids:
            placeholders = ",".join(["?"] * len(req.template_ids))
            sql = f"SELECT reaction_id FROM epd WHERE reaction_center_id IN ({placeholders})"
            cur = _execute_query(conn, is_pg, sql, tuple(req.template_ids))
            for row in cur.fetchall():
                all_reaction_ids.add(row[0])

        results = []
        for rid in sorted(all_reaction_ids):
            try:
                detail = get_reaction_detail(rid)
                taxonomy_code = (
                    detail["taxonomy"]["code"] if detail.get("taxonomy") else None
                )
                results.append(
                    {
                        "id": rid,
                        "case_id": detail["case_id"],
                        "reaction_name": detail["name"],
                        "canonical_smiles": detail["canonical_rsmi"],
                        "atom_mapped_smiles": detail["aam_key"],
                        "taxonomy_code": taxonomy_code,
                        "epd_lw": [
                            [
                                arr["arrow_type_code"],
                                arr["source_atoms"],
                                arr["target_atoms"],
                            ]
                            for arr in detail["arrows"]
                        ],
                    }
                )
            except Exception:
                continue

        return JSONResponse(
            content=results,
            headers={
                "Content-Disposition": "attachment; filename=synepd_reactions_export.json"
            },
        )
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
                its_graph = _deserialize_graph(raw_bytes)
                its_json = serialize_graph(its_graph)

            # Fetch taxonomy
            sql_tax = """
                SELECT t.code, t.name, t.level
                FROM reaction_taxonomy rt
                JOIN taxon t ON t.code = rt.taxon_code
                WHERE rt.reaction_id = ?
                ORDER BY t.code
            """
            cur = _execute_query(conn, is_pg, sql_tax, (rxn_id,))
            taxonomies = [
                {"code": row[0], "name": row[1], "level": row[2]}
                for row in cur.fetchall()
            ]
            if taxonomies:
                res["taxonomies"] = taxonomies
                res["taxonomy"] = taxonomies[0]
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


# Structured error response and custom handler
class APIError(BaseModel):
    code: str
    message: str


logger = logging.getLogger("synepd")


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    if exc.status_code == 500:
        eid = uuid.uuid4().hex[:8]
        logger.error("HTTP 500 Error [%s] on %s: %s", eid, request.url.path, exc.detail)
        return JSONResponse(
            status_code=500,
            content={
                "code": "INTERNAL_ERROR",
                "message": f"Internal server error (ref {eid})",
            },
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": "HTTP_ERROR", "message": exc.detail},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):
    eid = uuid.uuid4().hex[:8]
    logger.exception("Unhandled error [%s] on %s", eid, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"code": "INTERNAL_ERROR", "message": f"Internal error (ref {eid})"},
    )


@app.get("/api/health")
def health_check():
    """Lightweight liveness probe."""
    db_path = get_db_path_or_url()
    try:
        conn, _ = _get_connection(db_path)
        conn.execute("SELECT 1").fetchone()
        conn.close()
        return {"status": "ok", "db": db_path}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {e}")


@app.get("/api/reactions/{reaction_id}/neighbors")
def get_reaction_neighbors(reaction_id: int, limit: int = 10):
    """Return reactions that share the same reaction center (RC template) as reaction_id."""
    if limit < 1:
        limit = 10
    if limit > 50:
        limit = 50
    db_path = get_db_path_or_url()
    conn, is_pg = _get_connection(db_path)
    try:
        cur = conn.cursor()
        cur = _execute_query(
            conn, is_pg, "SELECT rc_id FROM its WHERE reaction_id = ?", (reaction_id,)
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(
                status_code=404, detail="No ITS found for this reaction"
            )
        rc_id = row[0]

        primary_taxon = _primary_taxon_sql("r")
        cur = _execute_query(
            conn,
            is_pg,
            f"""SELECT r.id, r.case_id, r.canonical_rsmi, r.name,
                      {primary_taxon} AS taxon_code
               FROM its i
               JOIN reaction r ON r.id = i.reaction_id
               WHERE i.rc_id = ? AND i.reaction_id != ?
               ORDER BY r.case_id
               LIMIT ?""",
            (rc_id, reaction_id, limit),
        )
        neighbors = [
            {
                "id": r[0],
                "case_id": r[1],
                "canonical_rsmi": r[2],
                "name": r[3],
                "taxonomy": r[4],
            }
            for r in cur.fetchall()
        ]
        return {"reaction_id": reaction_id, "rc_id": rc_id, "neighbors": neighbors}
    finally:
        conn.close()


@app.get("/api/reaction-centers")
def list_reaction_centers(limit: int = 50, offset: int = 0):
    """Return all unique reaction center templates with reaction counts."""
    db_path = get_db_path_or_url()
    conn, is_pg = _get_connection(db_path)
    try:
        cur = conn.cursor()
        try:
            cur = _execute_query(
                conn,
                is_pg,
                """SELECT rc.id, rc.wlhash, rc.smarts,
                          COUNT(i.reaction_id) AS reaction_count
                   FROM reaction_center rc
                   LEFT JOIN its i ON i.rc_id = rc.id
                   GROUP BY rc.id, rc.wlhash, rc.smarts
                   ORDER BY reaction_count DESC
                   LIMIT ? OFFSET ?""",
                (limit, offset),
            )
            results = [
                {"id": r[0], "wlhash": r[1], "smarts": r[2], "reaction_count": r[3]}
                for r in cur.fetchall()
            ]
        except Exception:
            cur = _execute_query(
                conn,
                is_pg,
                """SELECT rc.id, rc.wlhash,
                          COUNT(i.reaction_id) AS reaction_count
                   FROM reaction_center rc
                   LEFT JOIN its i ON i.rc_id = rc.id
                   GROUP BY rc.id, rc.wlhash
                   ORDER BY reaction_count DESC
                   LIMIT ? OFFSET ?""",
                (limit, offset),
            )
            results = [
                {"id": r[0], "wlhash": r[1], "smarts": None, "reaction_count": r[2]}
                for r in cur.fetchall()
            ]
        total = _execute_query(
            conn, is_pg, "SELECT COUNT(*) FROM reaction_center"
        ).fetchone()[0]
        return {"total": total, "results": results}
    finally:
        conn.close()


@app.get("/api/reaction-centers/{rc_id}/reactions")
def get_rc_reactions(rc_id: int, limit: int = 20, offset: int = 0):
    """Return paginated reactions for a given reaction center ID."""
    db_path = get_db_path_or_url()
    conn, is_pg = _get_connection(db_path)
    try:
        cur = conn.cursor()
        primary_taxon = _primary_taxon_sql("r")
        cur = _execute_query(
            conn,
            is_pg,
            f"""SELECT r.id, r.case_id, r.canonical_rsmi, r.name, {primary_taxon} AS taxon_code
               FROM its i
               JOIN reaction r ON r.id = i.reaction_id
               WHERE i.rc_id = ?
               ORDER BY r.case_id
               LIMIT ? OFFSET ?""",
            (rc_id, limit, offset),
        )
        results = [
            {
                "id": r[0],
                "case_id": r[1],
                "canonical_rsmi": r[2],
                "name": r[3],
                "taxonomy": r[4],
            }
            for r in cur.fetchall()
        ]
        total = _execute_query(
            conn, is_pg, "SELECT COUNT(*) FROM its WHERE rc_id = ?", (rc_id,)
        ).fetchone()[0]
        return {"total": total, "results": results}
    finally:
        conn.close()


@app.get("/api/arrow-types")
def get_arrow_types():
    """Return the EPD arrow type vocabulary."""
    db_path = get_db_path_or_url()
    conn, is_pg = _get_connection(db_path)
    try:
        cur = conn.cursor()
        cur = _execute_query(
            conn,
            is_pg,
            "SELECT code, source_type, target_type, electron_count, arrow_style "
            "FROM epd_arrow_type ORDER BY code",
        )
        return [
            {
                "code": r[0],
                "source_type": r[1],
                "target_type": r[2],
                "electron_count": r[3],
                "arrow_style": r[4],
            }
            for r in cur.fetchall()
        ]
    finally:
        conn.close()


@lru_cache(maxsize=4)
def _get_stats_cached(db_path: str, _db_token: str, _ttl_bucket: int) -> dict:
    conn, is_pg = _get_connection(db_path)
    try:

        def q(sql):
            return _execute_query(conn, is_pg, sql).fetchall()

        arrow_dist = {
            r[0]: r[1]
            for r in q(
                "SELECT arrow_type_code, COUNT(*) FROM epd_arrow GROUP BY arrow_type_code ORDER BY COUNT(*) DESC"
            )
        }
        arrow_count_dist = {
            r[0]: r[1]
            for r in q(
                "SELECT number_arrows, COUNT(*) FROM epd GROUP BY number_arrows ORDER BY number_arrows"
            )
        }
        taxonomy_level_dist = {r[0]: r[1] for r in q("""
            SELECT t.level, COUNT(DISTINCT t.code)
            FROM taxon t
            WHERE (t.code = 'POLAR' OR t.code LIKE 'POLAR.%')
              AND t.code != 'POLAR.99'
              AND t.code NOT LIKE 'POLAR.99.%'
              AND EXISTS (
                  SELECT 1
                  FROM reaction_taxonomy rt
                  WHERE rt.taxon_code = t.code OR rt.taxon_code LIKE t.code || '.%'
              )
            GROUP BY t.level
            ORDER BY t.level
            """)}
        rc_reuse_dist = {r[0]: r[1] for r in q("""SELECT reaction_count, COUNT(*) FROM (
                   SELECT rc.id, COUNT(i.reaction_id) AS reaction_count
                   FROM reaction_center rc
                   LEFT JOIN its i ON i.rc_id = rc.id
                   GROUP BY rc.id
               )
               GROUP BY reaction_count
               ORDER BY reaction_count""")}
        totals = q("""
            SELECT
                (SELECT COUNT(*) FROM reaction) AS reactions,
                (SELECT COUNT(*) FROM reaction_center) AS reaction_centers,
                (SELECT COUNT(DISTINCT t.code)
                 FROM taxon t
                 JOIN reaction_taxonomy rt ON rt.taxon_code = t.code
                 WHERE t.level = 4
                   AND t.code != 'POLAR.99'
                   AND t.code NOT LIKE 'POLAR.99.%') AS taxons,
                (SELECT COUNT(*) FROM molecule) AS molecules,
                (SELECT COUNT(*) FROM epd_arrow) AS epd_arrows,
                (SELECT COUNT(DISTINCT reaction_id) FROM reaction_taxonomy) AS classified_reactions
        """)[0]
        rc_count = q("SELECT COUNT(*) FROM reaction_center")[0][0]
        top_taxa = [
            {"code": r[0], "name": r[1], "count": r[2]}
            for r in q("""SELECT t.code, t.name, COUNT(DISTINCT rt.reaction_id) as cnt
               FROM taxon t JOIN reaction_taxonomy rt ON rt.taxon_code = t.code
               WHERE t.level = 4
                 AND t.code != 'POLAR.99'
                 AND t.code NOT LIKE 'POLAR.99.%'
               GROUP BY t.code, t.name ORDER BY cnt DESC LIMIT 10""")
        ]

        return {
            "totals": {
                "reactions": totals[0],
                "reaction_centers": totals[1],
                "taxons": totals[2],
                "molecules": totals[3],
                "epd_arrows": totals[4],
                "classified_reactions": totals[5],
            },
            "arrow_type_distribution": arrow_dist,
            "arrows_per_reaction_distribution": arrow_count_dist,
            "taxonomy_level_distribution": taxonomy_level_dist,
            "rc_reuse_distribution": rc_reuse_dist,
            "reaction_center_count": rc_count,
            "top_taxonomy_nodes": top_taxa,
        }
    finally:
        conn.close()


@app.get("/api/stats")
def get_stats(response: Response = None):
    if response is not None:
        response.headers["Cache-Control"] = "public, max-age=60"
    db_path = get_db_path_or_url()
    bucket = int(time.time() / _CACHE_TTL)
    try:
        return _get_stats_cached(db_path, db_cache_token(db_path), bucket)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/molecules/{inchikey}")
def get_molecule(inchikey: str):
    """Return molecule details and all reactions that contain it."""
    db_path = get_db_path_or_url()
    conn, is_pg = _get_connection(db_path)
    try:
        try:
            cur = _execute_query(
                conn,
                is_pg,
                "SELECT id, canonical_smiles, inchikey, iupac_name, cas_number FROM molecule WHERE inchikey = ?",
                (inchikey,),
            )
            mol = cur.fetchone()
            if not mol:
                raise HTTPException(status_code=404, detail="Molecule not found")
            iupac_val, cas_val = mol[3], mol[4]
        except Exception:
            cur = _execute_query(
                conn,
                is_pg,
                "SELECT id, canonical_smiles, inchikey FROM molecule WHERE inchikey = ?",
                (inchikey,),
            )
            mol = cur.fetchone()
            if not mol:
                raise HTTPException(status_code=404, detail="Molecule not found")
            iupac_val, cas_val = None, None

        cur = _execute_query(
            conn,
            is_pg,
            """SELECT r.id, r.case_id, r.canonical_rsmi, r.name, rc.side
               FROM reaction_component rc
               JOIN reaction r ON r.id = rc.reaction_id
               WHERE rc.molecule_id = ?
               ORDER BY r.case_id""",
            (mol[0],),
        )
        reactions = [
            {
                "id": r[0],
                "case_id": r[1],
                "canonical_rsmi": r[2],
                "name": r[3],
                "side": r[4],
            }
            for r in cur.fetchall()
        ]

        return {
            "id": mol[0],
            "canonical_smiles": mol[1],
            "inchikey": mol[2],
            "iupac_name": iupac_val,
            "cas_number": cas_val,
            "reactions": reactions,
        }
    finally:
        conn.close()


@app.get("/api/reactions/{reaction_id}/balance")
def check_balance(reaction_id: int):
    detail = get_reaction_detail(reaction_id)
    rsmi = detail.get("canonical_rsmi", "")
    try:
        from synepd.precheck.check_balance import check_reaction_balance

        bal = check_reaction_balance(rsmi)
        return {
            "balanced": bal.balanced,
            "atom_count_balanced": bal.atom_count_balanced,
            "charge_balanced": bal.charge_balanced,
            "reactant_atom_count": bal.reactant_atom_count,
            "product_atom_count": bal.product_atom_count,
            "reactant_formal_charge": bal.reactant_formal_charge,
            "product_formal_charge": bal.product_formal_charge,
            "errors": bal.errors(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class BalanceCheckRequest(BaseModel):
    rsmi: str


class SubmissionRequest(BaseModel):
    type: str
    label: str = ""
    rsmi: str = ""
    epd_lw: str = ""
    note: str = ""


@app.post("/api/check-balance")
def check_balance_smiles(req: BalanceCheckRequest):
    try:
        from synepd.precheck.check_balance import check_reaction_balance

        bal = check_reaction_balance(req.rsmi)
        return {
            "balanced": bal.balanced,
            "errors": bal.errors(),
            "reactant_atom_count": bal.reactant_atom_count,
            "product_atom_count": bal.product_atom_count,
            "reactant_formal_charge": bal.reactant_formal_charge,
            "product_formal_charge": bal.product_formal_charge,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/taxonomy/{code}/reactions")
def get_taxon_reactions(
    code: str,
    limit: int = 20,
    offset: int = 0,
    include_descendants: bool = False,
):
    """Return paginated reactions for a given taxonomy code."""
    if limit < 1:
        limit = 20
    if limit > 100:
        limit = 100
    if offset < 0:
        offset = 0
    db_path = get_db_path_or_url()
    conn, is_pg = _get_connection(db_path)
    try:
        if include_descendants:
            taxon_filter = "rt.taxon_code = ? OR rt.taxon_code LIKE ?"
            data_params = (code, f"{code}.%", limit, offset)
            count_params = (code, f"{code}.%")
        else:
            taxon_filter = "rt.taxon_code = ?"
            data_params = (code, limit, offset)
            count_params = (code,)

        sql = f"""
            SELECT r.id, r.case_id, r.canonical_rsmi, r.name, MIN(rt.taxon_code) AS taxon_code
            FROM reaction r
            JOIN reaction_taxonomy rt ON rt.reaction_id = r.id
            WHERE {taxon_filter}
            GROUP BY r.id, r.case_id, r.canonical_rsmi, r.name
            ORDER BY r.case_id
            LIMIT ? OFFSET ?
        """
        cur = _execute_query(conn, is_pg, sql, data_params)
        rows = [
            {
                "id": r[0],
                "case_id": r[1],
                "canonical_rsmi": r[2],
                "name": r[3],
                "taxonomy": r[4],
                "taxon_code": r[4],
            }
            for r in cur.fetchall()
        ]

        count_sql = f"SELECT COUNT(DISTINCT r.id) FROM reaction r JOIN reaction_taxonomy rt ON rt.reaction_id = r.id WHERE {taxon_filter}"
        total = _execute_query(conn, is_pg, count_sql, count_params).fetchone()[0]
        return {"code": code, "total": total, "results": rows}
    finally:
        conn.close()


@app.post("/api/submissions")
def create_submission(req: SubmissionRequest, request: Request):
    """Save a user-submitted reaction or issue report to a local cache DB."""
    if req.type not in ("reaction", "issue"):
        raise HTTPException(
            status_code=400, detail="type must be 'reaction' or 'issue'"
        )

    label = req.label.strip()
    rsmi = req.rsmi.strip()
    epd_lw = req.epd_lw.strip()
    note = req.note.strip()

    if not label:
        raise HTTPException(status_code=400, detail="label is required")
    if req.type == "reaction" and not rsmi:
        raise HTTPException(
            status_code=400, detail="rsmi is required for reaction submissions"
        )

    now = datetime.now(timezone.utc).isoformat()
    user_agent = request.headers.get("user-agent", "")[:200]

    try:
        conn = _get_submissions_conn()
        try:
            cur = conn.execute(
                """
                INSERT INTO submissions
                    (type, label, rsmi, epd_lw, note, submitted_at, user_agent)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    req.type,
                    label[:200],
                    rsmi[:2000],
                    epd_lw[:5000],
                    note[:1000],
                    now,
                    user_agent,
                ),
            )
            conn.commit()
            submission_id = cur.lastrowid
        finally:
            conn.close()
        return {"success": True, "submission_id": submission_id, "submitted_at": now}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save submission: {e}")


@app.get("/api/submissions")
def list_submissions(
    limit: int = 50,
    offset: int = 0,
    status: str = "pending",
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
):
    """Return cached submissions for local review."""
    admin_token = os.environ.get("SYNEPD_ADMIN_TOKEN")
    if admin_token and x_admin_token != admin_token:
        raise HTTPException(status_code=403, detail="Forbidden: Invalid Admin Token")
    if limit < 1:
        limit = 50
    if limit > 200:
        limit = 200
    if offset < 0:
        offset = 0

    try:
        conn = _get_submissions_conn()
        try:
            rows = conn.execute(
                """
                SELECT *
                FROM submissions
                WHERE status = ?
                ORDER BY submitted_at DESC
                LIMIT ? OFFSET ?
                """,
                (status, limit, offset),
            ).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) FROM submissions WHERE status = ?",
                (status,),
            ).fetchone()[0]
        finally:
            conn.close()
        return {"total": total, "results": [dict(row) for row in rows]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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


# Mount Sphinx documentation directory if it exists
docs_path = Path(__file__).parent.parent.parent / "docs" / "build" / "html"
if docs_path.exists():
    app.mount("/docs", StaticFiles(directory=docs_path, html=True), name="docs")
