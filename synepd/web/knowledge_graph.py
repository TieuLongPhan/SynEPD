"""
SynEPD Knowledge Graph API
==========================

A directed, multi-relational graph view over the existing relational schema.
No schema migration is required: every edge is derived from tables that the
release database already populates.

Node types (uid scheme keeps client-side de-duplication trivial):
    molecule   -> "m:<molecule_id>"
    reaction   -> "r:<reaction_id>"
    template   -> "t:<rc_id>"           (reaction-centre / "template X")
    taxon      -> "x:<taxon_code>"

Directed edges (semantics chosen to read left-to-right as the chemistry flows):
    m:A  --reactant-->  r:R     A is consumed by reaction R
    r:R  --product -->  m:B     R produces molecule B
    r:R  --template-->  t:X     R is an instance of template X
    r:R  --class   -->  x:TAX   R is classified under taxon TAX

The graph is explored lazily: the client fetches an "ego" subgraph for one
seed node, then expands by fetching the ego subgraph of any clicked node and
merging the new nodes/links (de-duplicated by uid). This keeps payloads small
and avoids hairballs on highly-connected hubs.
"""

from __future__ import annotations

import os
import threading
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query

from synepd.core.query import _get_connection, _execute_query

router = APIRouter(prefix="/api/kg", tags=["knowledge-graph"])

# --------------------------------------------------------------------------- #
# Config / helpers
# --------------------------------------------------------------------------- #

DEFAULT_MAX_REACTIONS = 20
HARD_MAX_REACTIONS = 100

# --------------------------------------------------------------------------- #
# Reaction fingerprint cache (built lazily on first similarity request)
# --------------------------------------------------------------------------- #
_rfp_lock = threading.Lock()
_rfp_ids: Optional[List[int]] = None
_rfp_matrix = None  # np.ndarray of shape (N, 1024) once built


def _ensure_rfp_matrix():
    """Build (once) a numpy matrix of synrfp fingerprints for all reactions."""
    global _rfp_ids, _rfp_matrix
    if _rfp_matrix is not None:
        return _rfp_ids, _rfp_matrix
    with _rfp_lock:
        if _rfp_matrix is not None:  # re-check inside lock
            return _rfp_ids, _rfp_matrix
        import numpy as np
        from synrfp import synrfp as make_rfp

        conn, is_pg = _get_connection(_db_path())
        try:
            cur = _execute_query(
                conn,
                is_pg,
                "SELECT id, canonical_rsmi FROM reaction WHERE canonical_rsmi IS NOT NULL",
                (),
            )
            rows = cur.fetchall()
        finally:
            conn.close()

        ids, fps = [], []
        for rxn_id, rsmi in rows:
            try:
                fp = make_rfp(rsmi)
                ids.append(rxn_id)
                fps.append(fp)
            except Exception:
                pass

        _rfp_ids = ids
        _rfp_matrix = np.array(fps, dtype=np.uint8)  # (N, 1024)
    return _rfp_ids, _rfp_matrix


def _top_k_similar(query_rsmi: str, top_k: int) -> List[Tuple[float, int]]:
    """Return [(tanimoto, reaction_id), ...] sorted descending, length ≤ top_k."""
    import numpy as np
    from synrfp import synrfp as make_rfp

    query_fp = np.array(make_rfp(query_rsmi), dtype=np.uint8)  # (1024,)
    ids, matrix = _ensure_rfp_matrix()  # (N, 1024)
    if not ids:
        return []
    inter = matrix @ query_fp  # (N,) dot = AND popcnt for 0/1
    union = matrix.sum(axis=1) + int(query_fp.sum()) - inter
    tanimoto = np.where(union > 0, inter / union, 0.0)
    top_idx = np.argsort(tanimoto)[::-1][:top_k]
    return [(float(tanimoto[i]), ids[i]) for i in top_idx if tanimoto[i] > 0.0]


def _db_path() -> str:
    return os.environ.get("SYNEPD_DATABASE_URL", "data/epdb.sqlite")


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(value, hi))


def _normalize_query(raw: str) -> str:
    """Strip atom-map numbers so a pasted mapped SMILES still matches the
    flattened canonical_smiles stored in the molecule table. Falls back to the
    raw string if RDKit is unavailable."""
    raw = (raw or "").strip()
    if not raw:
        return raw
    if ":" not in raw:
        return raw
    try:
        from synepd.core.query import strip_atom_map

        # Only attempt on something that looks like a single species
        if ">>" not in raw and "." not in raw:
            flat = strip_atom_map(raw)
            if flat:
                return flat
    except Exception:
        pass
    # generic fallback: drop ":<n>" tokens
    import re

    return re.sub(r":\d+", "", raw)


def _mol_label(smiles: Optional[str]) -> str:
    if not smiles:
        return "?"
    return smiles if len(smiles) <= 32 else smiles[:29] + "..."


def _molecule_node(
    mol_id: int,
    smiles: Optional[str],
    inchikey: Optional[str],
    reaction_count: Optional[int] = None,
) -> Dict[str, Any]:
    node = {
        "id": f"m:{mol_id}",
        "type": "molecule",
        "label": _mol_label(smiles),
        "smiles": smiles,
        "inchikey": inchikey,
        "ref_id": mol_id,
        "expandable": True,
    }
    if reaction_count is not None:
        node["reaction_count"] = reaction_count
    return node


def _reaction_node(
    rxn_id: int,
    case_id: Optional[str],
    name: Optional[str],
    taxon: Optional[str] = None,
    rc_id: Optional[int] = None,
    wlhash: Optional[str] = None,
    rsmi: Optional[str] = None,
) -> Dict[str, Any]:
    """The CRN "reaction" node. It is keyed per reaction (so reactant/product
    pairing stays correct) but is *presented* as its mechanistic template:
    reactants -> [Template N] -> products. The reaction name/case id are kept
    so the click-info panel can still show them, but they are not the label."""
    template_label = (
        f"Template {rc_id}"
        if rc_id is not None
        else (name or case_id or f"reaction {rxn_id}")
    )
    return {
        "id": f"r:{rxn_id}",
        "type": "reaction",
        "label": template_label,
        "rc_id": rc_id,
        "wlhash": wlhash,
        "case_id": case_id,
        "name": name,
        "taxon": taxon,
        "rsmi": rsmi,
        "ref_id": rxn_id,
        "expandable": True,
        "openable": True,
    }


def _template_node(
    rc_id: int, wlhash: Optional[str] = None, reaction_count: Optional[int] = None
) -> Dict[str, Any]:
    node = {
        "id": f"t:{rc_id}",
        "type": "template",
        "label": f"Template {rc_id}",
        "wlhash": wlhash,
        "ref_id": rc_id,
        "expandable": True,
    }
    if reaction_count is not None:
        node["reaction_count"] = reaction_count
    return node


def _taxon_node(code: str, name: Optional[str] = None) -> Dict[str, Any]:
    return {
        "id": f"x:{code}",
        "type": "taxon",
        "label": name or code,
        "code": code,
        "ref_id": code,
        "expandable": True,
    }


class _GraphAccumulator:
    """Collects unique nodes and links for one ego response."""

    def __init__(self) -> None:
        self._nodes: Dict[str, Dict[str, Any]] = {}
        self._links: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    def add_node(self, node: Dict[str, Any]) -> None:
        uid = node["id"]
        # keep the richest version of a node (e.g. one that carries a count)
        existing = self._nodes.get(uid)
        if existing is None:
            self._nodes[uid] = node
        else:
            for k, v in node.items():
                if v is not None and existing.get(k) is None:
                    existing[k] = v

    def add_link(self, source: str, target: str, relation: str, **extra: Any) -> None:
        key = (source, target, relation)
        if key not in self._links:
            link = {"source": source, "target": target, "relation": relation}
            link.update(extra)
            self._links[key] = link

    def result(self, root_uid: str) -> Dict[str, Any]:
        if root_uid in self._nodes:
            self._nodes[root_uid]["root"] = True
        return {
            "root": root_uid,
            "nodes": list(self._nodes.values()),
            "links": list(self._links.values()),
        }


def _primary_taxon(conn, is_pg, reaction_ids: List[int]) -> Dict[int, str]:
    if not reaction_ids:
        return {}
    placeholders = ",".join("?" for _ in reaction_ids)
    cur = _execute_query(
        conn,
        is_pg,
        f"""SELECT reaction_id, MIN(taxon_code)
            FROM reaction_taxonomy
            WHERE reaction_id IN ({placeholders})
            GROUP BY reaction_id""",
        tuple(reaction_ids),
    )
    return {row[0]: row[1] for row in cur.fetchall()}


def _expand_reactions(
    conn,
    is_pg,
    acc: _GraphAccumulator,
    reaction_rows: List[Tuple[int, str, str]],
    include_taxonomy: bool,
    seed_uid: Optional[str] = None,
    seed_side: Optional[Dict[int, str]] = None,
) -> None:
    """Given a list of (reaction_id, case_id, name), attach every reaction's
    components, template, and (optionally) taxonomy to the accumulator.

    seed_side maps reaction_id -> side of the seed molecule, used to orient the
    edge to the seed node correctly when the seed itself is a molecule."""
    if not reaction_rows:
        return

    reaction_ids = [r[0] for r in reaction_rows]
    # Always fetch primary taxon so the client can filter by POLAR class even
    # when taxonomy graph nodes are toggled off.
    tax_map = _primary_taxon(conn, is_pg, reaction_ids)
    placeholders = ",".join("?" for _ in reaction_ids)

    # Template (reaction-centre) per reaction. In the CRN view the reaction node
    # IS the template, so we look this up first and fold it into the node.
    rc_map: Dict[int, Tuple[int, Optional[str]]] = {}
    cur = _execute_query(
        conn,
        is_pg,
        f"""SELECT i.reaction_id, i.rc_id, i.wlhash
            FROM its i
            WHERE i.reaction_id IN ({placeholders})""",
        tuple(reaction_ids),
    )
    for r_id, rc_id, wlhash in cur.fetchall():
        rc_map[r_id] = (rc_id, wlhash)

    rsmi_map: Dict[int, str] = {}
    cur = _execute_query(
        conn,
        is_pg,
        f"SELECT id, canonical_rsmi FROM reaction WHERE id IN ({placeholders})",
        tuple(reaction_ids),
    )
    for r_id, rsmi in cur.fetchall():
        if rsmi:
            rsmi_map[r_id] = rsmi

    for rxn_id, case_id, name in reaction_rows:
        rc_id, wlhash = rc_map.get(rxn_id, (None, None))
        acc.add_node(
            _reaction_node(
                rxn_id,
                case_id,
                name,
                tax_map.get(rxn_id),
                rc_id=rc_id,
                wlhash=wlhash,
                rsmi=rsmi_map.get(rxn_id),
            )
        )

    # Components for all reactions in one query
    cur = _execute_query(
        conn,
        is_pg,
        f"""SELECT rc.reaction_id, rc.side, m.id, m.canonical_smiles, m.inchikey
            FROM reaction_component rc
            JOIN molecule m ON m.id = rc.molecule_id
            WHERE rc.reaction_id IN ({placeholders})
            ORDER BY rc.reaction_id, rc.side, rc.component_index""",
        tuple(reaction_ids),
    )
    for rxn_id, side, mol_id, smiles, inchikey in cur.fetchall():
        rxn_uid = f"r:{rxn_id}"
        mol_uid = f"m:{mol_id}"
        acc.add_node(_molecule_node(mol_id, smiles, inchikey))
        if side == "reactant":
            acc.add_link(mol_uid, rxn_uid, "reactant")
        else:
            acc.add_link(rxn_uid, mol_uid, "product")

    # (CRN view: no separate template node — the reaction node already carries
    #  its template identity, so reactants -> [Template N] -> products.)

    # Taxonomy edges + taxon nodes (optional — taxon data is always fetched above
    # for reaction-node enrichment, but graph nodes are only added when requested)
    if include_taxonomy and tax_map:
        codes = sorted(set(tax_map.values()))
        ph = ",".join("?" for _ in codes)
        cur = _execute_query(
            conn,
            is_pg,
            f"SELECT code, name FROM taxon WHERE code IN ({ph})",
            tuple(codes),
        )
        names = {row[0]: row[1] for row in cur.fetchall()}
        for rxn_id, code in tax_map.items():
            acc.add_node(_taxon_node(code, names.get(code)))
            acc.add_link(f"r:{rxn_id}", f"x:{code}", "class")


# --------------------------------------------------------------------------- #
# Ego builders
# --------------------------------------------------------------------------- #


def build_molecule_ego(
    conn, is_pg, molecule_id: int, max_reactions: int, include_taxonomy: bool
) -> Dict[str, Any]:
    cur = _execute_query(
        conn,
        is_pg,
        "SELECT id, canonical_smiles, inchikey FROM molecule WHERE id = ?",
        (molecule_id,),
    )
    mol = cur.fetchone()
    if not mol:
        raise HTTPException(status_code=404, detail="Molecule not found")

    cur = _execute_query(
        conn,
        is_pg,
        "SELECT COUNT(*) FROM reaction_component WHERE molecule_id = ?",
        (molecule_id,),
    )
    total_reactions = cur.fetchone()[0]

    acc = _GraphAccumulator()
    seed_uid = f"m:{molecule_id}"
    acc.add_node(_molecule_node(mol[0], mol[1], mol[2], total_reactions))

    cur = _execute_query(
        conn,
        is_pg,
        """SELECT rc.reaction_id, rc.side, r.case_id, r.name
           FROM reaction_component rc
           JOIN reaction r ON r.id = rc.reaction_id
           WHERE rc.molecule_id = ?
           ORDER BY rc.reaction_id
           LIMIT ?""",
        (molecule_id, max_reactions),
    )
    rows = cur.fetchall()
    reaction_rows = [(r[0], r[2], r[3]) for r in rows]

    _expand_reactions(conn, is_pg, acc, reaction_rows, include_taxonomy)

    result = acc.result(seed_uid)
    result["truncated"] = total_reactions > len(reaction_rows)
    result["total_reactions"] = total_reactions
    return result


def build_reaction_ego(
    conn, is_pg, reaction_id: int, include_taxonomy: bool
) -> Dict[str, Any]:
    cur = _execute_query(
        conn,
        is_pg,
        "SELECT id, case_id, name FROM reaction WHERE id = ?",
        (reaction_id,),
    )
    rxn = cur.fetchone()
    if not rxn:
        raise HTTPException(status_code=404, detail="Reaction not found")

    acc = _GraphAccumulator()
    seed_uid = f"r:{reaction_id}"
    _expand_reactions(
        conn,
        is_pg,
        acc,
        [(rxn[0], rxn[1], rxn[2])],
        include_taxonomy,
    )
    result = acc.result(seed_uid)
    result["truncated"] = False
    return result


def build_template_ego(
    conn, is_pg, rc_id: int, max_reactions: int, include_taxonomy: bool
) -> Dict[str, Any]:
    cur = _execute_query(
        conn,
        is_pg,
        "SELECT id, wlhash FROM reaction_center WHERE id = ?",
        (rc_id,),
    )
    rc = cur.fetchone()
    if not rc:
        raise HTTPException(status_code=404, detail="Template not found")

    cur = _execute_query(
        conn,
        is_pg,
        "SELECT COUNT(*) FROM its WHERE rc_id = ?",
        (rc_id,),
    )
    total_reactions = cur.fetchone()[0]

    acc = _GraphAccumulator()

    cur = _execute_query(
        conn,
        is_pg,
        """SELECT i.reaction_id, r.case_id, r.name
           FROM its i
           JOIN reaction r ON r.id = i.reaction_id
           WHERE i.rc_id = ?
           ORDER BY r.case_id
           LIMIT ?""",
        (rc_id, max_reactions),
    )
    reaction_rows = [(r[0], r[1], r[2]) for r in cur.fetchall()]
    # CRN view: no separate template node. We surface every reaction that shares
    # this template (each rendered as "Template N") plus its reactants/products,
    # so the user sees the whole mechanistic family as one connected sub-network.
    _expand_reactions(conn, is_pg, acc, reaction_rows, include_taxonomy)

    # Root on the first sibling reaction so the client can centre the view.
    seed_uid = f"r:{reaction_rows[0][0]}" if reaction_rows else f"t:{rc_id}"
    result = acc.result(seed_uid)
    result["truncated"] = total_reactions > len(reaction_rows)
    result["total_reactions"] = total_reactions
    result["rc_id"] = rc_id
    return result


# --------------------------------------------------------------------------- #
# Similarity helpers used by routes
# --------------------------------------------------------------------------- #


def _fetch_reaction_rows(
    conn, is_pg, rxn_ids: List[int]
) -> Dict[int, Tuple[Optional[str], Optional[str]]]:
    """Return {reaction_id: (case_id, name)} for the given ids."""
    if not rxn_ids:
        return {}
    ph = ",".join("?" for _ in rxn_ids)
    cur = _execute_query(
        conn,
        is_pg,
        f"SELECT id, case_id, name FROM reaction WHERE id IN ({ph})",
        tuple(rxn_ids),
    )
    return {r[0]: (r[1], r[2]) for r in cur.fetchall()}


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@router.get("/search")
def kg_search(q: str = Query(..., min_length=1), limit: int = 15):
    """Find seed nodes for the knowledge graph.

    Matches molecules (by SMILES / InChIKey) and reactions (by case id / name).
    Returns lightweight node descriptors the client can use to seed the graph.
    """
    limit = _clamp(limit, 1, 50)
    raw = q.strip()
    norm = _normalize_query(raw)
    conn, is_pg = _get_connection(_db_path())
    try:
        results: List[Dict[str, Any]] = []
        seen = set()

        # --- molecules ---------------------------------------------------- #
        cur = _execute_query(
            conn,
            is_pg,
            """SELECT m.id, m.canonical_smiles, m.inchikey,
                      (SELECT COUNT(*) FROM reaction_component rc
                       WHERE rc.molecule_id = m.id) AS rc_count
               FROM molecule m
               WHERE m.canonical_smiles = ?
                  OR m.inchikey = ?
                  OR m.canonical_smiles LIKE ?
                  OR m.inchikey LIKE ?
               ORDER BY (m.canonical_smiles = ?) DESC, rc_count DESC
               LIMIT ?""",
            (norm, raw, f"%{norm}%", f"{raw}%", norm, limit),
        )
        for mid, smiles, inchikey, rc_count in cur.fetchall():
            uid = f"m:{mid}"
            if uid in seen:
                continue
            seen.add(uid)
            results.append(_molecule_node(mid, smiles, inchikey, rc_count))

        # --- reactions (fill remaining slots) ----------------------------- #
        remaining = limit - len(results)
        if remaining > 0:
            cur = _execute_query(
                conn,
                is_pg,
                """SELECT id, case_id, name FROM reaction
                   WHERE UPPER(case_id) LIKE ? OR name LIKE ?
                   ORDER BY case_id
                   LIMIT ?""",
                (f"%{raw.upper()}%", f"%{raw}%", remaining),
            )
            for rid, case_id, name in cur.fetchall():
                uid = f"r:{rid}"
                if uid in seen:
                    continue
                seen.add(uid)
                results.append(_reaction_node(rid, case_id, name))

        return {"query": raw, "normalized": norm, "results": results}
    finally:
        conn.close()


@router.get("/node/molecule/{molecule_id}")
def kg_molecule(
    molecule_id: int,
    max_reactions: int = DEFAULT_MAX_REACTIONS,
    include_taxonomy: bool = False,
):
    max_reactions = _clamp(max_reactions, 1, HARD_MAX_REACTIONS)
    conn, is_pg = _get_connection(_db_path())
    try:
        return build_molecule_ego(
            conn, is_pg, molecule_id, max_reactions, include_taxonomy
        )
    finally:
        conn.close()


@router.get("/molecule-info/{molecule_id}")
def kg_molecule_info(molecule_id: int, limit: int = 30):
    """Details for the click-to-inspect molecule panel: SMILES, InChIKey, and
    every reaction the molecule takes part in (with the side it appears on).
    Keyed by molecule_id so it works even when inchikey is null."""
    limit = _clamp(limit, 1, 200)
    conn, is_pg = _get_connection(_db_path())
    try:
        cur = _execute_query(
            conn,
            is_pg,
            "SELECT id, canonical_smiles, inchikey FROM molecule WHERE id = ?",
            (molecule_id,),
        )
        mol = cur.fetchone()
        if not mol:
            raise HTTPException(status_code=404, detail="Molecule not found")

        cur = _execute_query(
            conn,
            is_pg,
            "SELECT COUNT(*) FROM reaction_component WHERE molecule_id = ?",
            (molecule_id,),
        )
        total = cur.fetchone()[0]

        cur = _execute_query(
            conn,
            is_pg,
            """SELECT r.id, r.case_id, r.name, rc.side
               FROM reaction_component rc
               JOIN reaction r ON r.id = rc.reaction_id
               WHERE rc.molecule_id = ?
               ORDER BY rc.side, r.case_id
               LIMIT ?""",
            (molecule_id, limit),
        )
        reactions = [
            {"id": r[0], "case_id": r[1], "name": r[2], "side": r[3]}
            for r in cur.fetchall()
        ]
        return {
            "id": mol[0],
            "canonical_smiles": mol[1],
            "inchikey": mol[2],
            "total_reactions": total,
            "reactions": reactions,
        }
    finally:
        conn.close()


@router.get("/node/reaction/{reaction_id}")
def kg_reaction(reaction_id: int, include_taxonomy: bool = False):
    conn, is_pg = _get_connection(_db_path())
    try:
        return build_reaction_ego(conn, is_pg, reaction_id, include_taxonomy)
    finally:
        conn.close()


@router.get("/node/template/{rc_id}")
def kg_template(
    rc_id: int,
    max_reactions: int = DEFAULT_MAX_REACTIONS,
    include_taxonomy: bool = False,
):
    max_reactions = _clamp(max_reactions, 1, HARD_MAX_REACTIONS)
    conn, is_pg = _get_connection(_db_path())
    try:
        return build_template_ego(conn, is_pg, rc_id, max_reactions, include_taxonomy)
    finally:
        conn.close()


@router.get("/expand")
def kg_expand(
    type: str,
    id: str,
    max_reactions: int = DEFAULT_MAX_REACTIONS,
    include_taxonomy: bool = False,
):
    """Unified expansion dispatcher. `id` is the raw ref id (numeric for
    molecule/reaction/template, taxon code for taxon)."""
    max_reactions = _clamp(max_reactions, 1, HARD_MAX_REACTIONS)
    conn, is_pg = _get_connection(_db_path())
    try:
        if type == "molecule":
            return build_molecule_ego(
                conn, is_pg, int(id), max_reactions, include_taxonomy
            )
        if type == "reaction":
            return build_reaction_ego(conn, is_pg, int(id), include_taxonomy)
        if type == "template":
            return build_template_ego(
                conn, is_pg, int(id), max_reactions, include_taxonomy
            )
        if type == "taxon":
            acc = _GraphAccumulator()
            seed_uid = f"x:{id}"
            cur = _execute_query(
                conn, is_pg, "SELECT code, name FROM taxon WHERE code = ?", (id,)
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Taxon not found")
            acc.add_node(_taxon_node(row[0], row[1]))
            cur = _execute_query(
                conn,
                is_pg,
                """SELECT r.id, r.case_id, r.name
                   FROM reaction_taxonomy rt
                   JOIN reaction r ON r.id = rt.reaction_id
                   WHERE rt.taxon_code = ?
                   ORDER BY r.case_id
                   LIMIT ?""",
                (id, max_reactions),
            )
            rows = cur.fetchall()
            for rid, case_id, name in rows:
                acc.add_node(_reaction_node(rid, case_id, name))
                acc.add_link(f"r:{rid}", seed_uid, "class")
            return acc.result(seed_uid)
        raise HTTPException(status_code=400, detail=f"Unknown node type: {type}")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid node id")
    finally:
        conn.close()


@router.get("/similar-reactions")
def kg_similar_reactions(
    rsmi: str = Query(..., description="Reaction SMILES to compare against"),
    top_k: int = 15,
):
    """Return the top-k reactions most similar to a given reaction SMILES,
    ranked by Tanimoto similarity of their synrfp fingerprints.

    The fingerprint matrix is computed once (lazily) and cached in memory for
    the lifetime of the server process — typically sub-second after warm-up."""
    top_k = _clamp(top_k, 1, 50)
    try:
        hits = _top_k_similar(rsmi, top_k)
    except Exception as exc:
        raise HTTPException(
            status_code=422, detail=f"Could not fingerprint reaction SMILES: {exc}"
        )

    if not hits:
        return {"query": rsmi, "results": [], "total": 0}

    rxn_ids = [rxn_id for _, rxn_id in hits]
    conn, is_pg = _get_connection(_db_path())
    try:
        rxn_map = _fetch_reaction_rows(conn, is_pg, rxn_ids)
    finally:
        conn.close()

    results = []
    for score, rxn_id in hits:
        case_id, name = rxn_map.get(rxn_id, (None, None))
        node = _reaction_node(rxn_id, case_id, name)
        node["similarity"] = round(score, 3)
        results.append(node)

    return {"query": rsmi, "results": results, "total": len(results)}


@router.get("/reactions-by-wlhash")
def kg_reactions_by_wlhash(
    wlhash: str = Query(..., description="WL-hash of the reaction centre"),
    max_reactions: int = DEFAULT_MAX_REACTIONS,
):
    """Return all reactions whose ITS reaction centre matches the given WL-hash.
    This is an exact reaction-centre match — identical mechanism, different
    substrates.  Free: no fingerprinting, direct index lookup."""
    max_reactions = _clamp(max_reactions, 1, HARD_MAX_REACTIONS)
    conn, is_pg = _get_connection(_db_path())
    try:
        cur = _execute_query(
            conn,
            is_pg,
            "SELECT COUNT(*) FROM its WHERE wlhash = ?",
            (wlhash,),
        )
        total = cur.fetchone()[0]
        if total == 0:
            return {"wlhash": wlhash, "results": [], "total": 0}

        cur = _execute_query(
            conn,
            is_pg,
            """SELECT r.id, r.case_id, r.name
               FROM its i
               JOIN reaction r ON r.id = i.reaction_id
               WHERE i.wlhash = ?
               ORDER BY r.case_id
               LIMIT ?""",
            (wlhash, max_reactions),
        )
        results = [_reaction_node(r[0], r[1], r[2]) for r in cur.fetchall()]
        return {
            "wlhash": wlhash,
            "results": results,
            "total": total,
            "truncated": total > max_reactions,
        }
    finally:
        conn.close()


@router.get("/substructure-search")
def kg_substructure_search(
    smarts: str = Query(..., description="SMARTS pattern to match against molecules"),
    max_hits: int = 50,
):
    """Return molecule nodes whose canonical SMILES contains the given SMARTS
    substructure.  Uses RDKit directly; max 200 hits."""
    from rdkit import Chem

    max_hits = _clamp(max_hits, 1, 200)
    try:
        pat = Chem.MolFromSmarts(smarts)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid SMARTS: {exc}")
    if pat is None:
        raise HTTPException(status_code=422, detail="Invalid SMARTS pattern")

    conn, is_pg = _get_connection(_db_path())
    try:
        cur = _execute_query(
            conn,
            is_pg,
            "SELECT id, canonical_smiles, inchikey FROM molecule WHERE canonical_smiles IS NOT NULL",
            (),
        )
        all_mols = cur.fetchall()
    finally:
        conn.close()

    hits: List[Tuple[int, str, Optional[str]]] = []
    for mol_id, smiles, inchikey in all_mols:
        if len(hits) >= max_hits:
            break
        try:
            mol = Chem.MolFromSmiles(smiles)
            if mol and mol.HasSubstructMatch(pat):
                hits.append((mol_id, smiles, inchikey))
        except Exception:
            pass

    if not hits:
        return {"smarts": smarts, "results": [], "total": 0, "truncated": False}

    # Enrich with reaction counts
    hit_ids = [h[0] for h in hits]
    conn, is_pg = _get_connection(_db_path())
    try:
        ph = ",".join("?" for _ in hit_ids)
        cur = _execute_query(
            conn,
            is_pg,
            f"SELECT molecule_id, COUNT(*) FROM reaction_component WHERE molecule_id IN ({ph}) GROUP BY molecule_id",
            tuple(hit_ids),
        )
        rc_counts = {r[0]: r[1] for r in cur.fetchall()}
    finally:
        conn.close()

    results = [
        _molecule_node(mol_id, smiles, inchikey, rc_counts.get(mol_id, 0))
        for mol_id, smiles, inchikey in hits
    ]
    return {
        "smarts": smarts,
        "results": results,
        "total": len(results),
        "truncated": len(hits) >= max_hits,
    }


# --------------------------------------------------------------------------- #
# Path finding  (BFS over the reaction network)
# --------------------------------------------------------------------------- #

_rxn_graph_lock = threading.Lock()
_rxn_graph: Optional[Dict] = None


def _ensure_rxn_graph() -> Dict:
    """Lazy-build an in-memory adjacency from reaction_component.
    mol_reactions : mol_id -> [(rxn_id, side), ...]
    rxn_mols      : rxn_id -> {side: [mol_id, ...]}
    """
    global _rxn_graph
    if _rxn_graph is not None:
        return _rxn_graph
    with _rxn_graph_lock:
        if _rxn_graph is not None:
            return _rxn_graph
        conn, is_pg = _get_connection(_db_path())
        try:
            cur = _execute_query(
                conn,
                is_pg,
                "SELECT reaction_id, molecule_id, side FROM reaction_component",
                (),
            )
            rows = cur.fetchall()
        finally:
            conn.close()

        mol_reactions: Dict[int, List[Tuple[int, str]]] = {}
        rxn_mols: Dict[int, Dict[str, List[int]]] = {}
        for rxn_id, mol_id, side in rows:
            mol_reactions.setdefault(mol_id, []).append((rxn_id, side))
            rxn_mols.setdefault(rxn_id, {"reactant": [], "product": []})
            rxn_mols[rxn_id][side].append(mol_id)

        _rxn_graph = {"mol_reactions": mol_reactions, "rxn_mols": rxn_mols}
        return _rxn_graph


def _bfs_path(
    start_mol_id: int,
    end_mol_id: int,
    mode: str = "both",
    max_depth: int = 8,
) -> Optional[List]:
    """BFS returning [mol_id, rxn_id, mol_id, rxn_id, ..., mol_id] or None.

    mode='forward'  mol must be a reactant; next mol is a product.
    mode='retro'    mol must be a product;   next mol is a reactant.
    mode='both'     undirected — traverse whichever side applies.
    """
    if start_mol_id == end_mol_id:
        return [start_mol_id]

    graph = _ensure_rxn_graph()
    mol_reactions = graph["mol_reactions"]
    rxn_mols = graph["rxn_mols"]

    # queue entries: (current_mol_id, path_so_far)
    queue: deque = deque([(start_mol_id, [start_mol_id])])
    visited: set = {start_mol_id}

    while queue:
        mol_id, path = queue.popleft()
        if len(path) >= max_depth * 2 + 1:
            continue

        for rxn_id, side in mol_reactions.get(mol_id, []):
            if mode == "forward" and side != "reactant":
                continue
            if mode == "retro" and side != "product":
                continue

            # Which side do we go to next?
            if mode == "retro":
                next_side = "reactant"
            elif mode == "forward":
                next_side = "product"
            else:  # both — go to the opposite side
                next_side = "product" if side == "reactant" else "reactant"

            for next_mol_id in rxn_mols.get(rxn_id, {}).get(next_side, []):
                new_path = path + [rxn_id, next_mol_id]
                if next_mol_id == end_mol_id:
                    return new_path
                if next_mol_id not in visited:
                    visited.add(next_mol_id)
                    queue.append((next_mol_id, new_path))

    return None


def _resolve_mol_id(conn, is_pg, query: str) -> Optional[Tuple[int, str, str]]:
    """Resolve SMILES / InChIKey query to (mol_id, canonical_smiles, inchikey)."""
    raw = query.strip()
    norm = _normalize_query(raw)
    cur = _execute_query(
        conn,
        is_pg,
        "SELECT id, canonical_smiles, inchikey FROM molecule "
        "WHERE canonical_smiles = ? OR inchikey = ? LIMIT 1",
        (norm, raw),
    )
    row = cur.fetchone()
    if row:
        return row
    # Try RDKit canonicalization so user can paste any valid SMILES
    try:
        from rdkit import Chem

        mol = Chem.MolFromSmiles(raw)
        if mol:
            canon = Chem.MolToSmiles(mol)
            cur = _execute_query(
                conn,
                is_pg,
                "SELECT id, canonical_smiles, inchikey FROM molecule "
                "WHERE canonical_smiles = ? LIMIT 1",
                (canon,),
            )
            row = cur.fetchone()
            if row:
                return row
    except Exception:
        pass
    return None


def _path_to_graph(conn, is_pg, path: List, mode: str) -> Dict[str, Any]:
    """Convert BFS path to KG-compatible nodes + links with on_path=True."""
    mol_ids = [path[i] for i in range(0, len(path), 2)]
    rxn_ids = [path[i] for i in range(1, len(path), 2)]
    path_uids: List[str] = []

    nodes: List[Dict] = []

    # Molecule nodes
    ph = ",".join("?" for _ in mol_ids)
    cur = _execute_query(
        conn,
        is_pg,
        f"SELECT id, canonical_smiles, inchikey FROM molecule WHERE id IN ({ph})",
        tuple(mol_ids),
    )
    for mid, smiles, inchikey in cur.fetchall():
        node = _molecule_node(mid, smiles, inchikey)
        node["on_path"] = True
        nodes.append(node)
        path_uids.append(f"m:{mid}")

    # Reaction nodes
    if rxn_ids:
        ph2 = ",".join("?" for _ in rxn_ids)
        cur = _execute_query(
            conn,
            is_pg,
            f"""SELECT r.id, r.case_id, r.name, r.canonical_rsmi,
                       i.rc_id, i.wlhash
                FROM reaction r
                LEFT JOIN its i ON i.reaction_id = r.id
                WHERE r.id IN ({ph2})""",
            tuple(rxn_ids),
        )
        for rid, case_id, name, rsmi, rc_id, wlhash in cur.fetchall():
            node = _reaction_node(
                rid, case_id, name, rc_id=rc_id, wlhash=wlhash, rsmi=rsmi
            )
            node["on_path"] = True
            nodes.append(node)
            path_uids.append(f"r:{rid}")

    # Links along the path
    links: List[Dict] = []
    for i in range(0, len(path) - 2, 2):
        mol_id = path[i]
        rxn_id = path[i + 1]
        next_mol_id = path[i + 2]
        if mode == "retro":
            links.append(
                {
                    "source": f"m:{mol_id}",
                    "target": f"r:{rxn_id}",
                    "relation": "product",
                    "on_path": True,
                }
            )
            links.append(
                {
                    "source": f"m:{next_mol_id}",
                    "target": f"r:{rxn_id}",
                    "relation": "reactant",
                    "on_path": True,
                }
            )
        else:
            links.append(
                {
                    "source": f"m:{mol_id}",
                    "target": f"r:{rxn_id}",
                    "relation": "reactant",
                    "on_path": True,
                }
            )
            links.append(
                {
                    "source": f"r:{rxn_id}",
                    "target": f"m:{next_mol_id}",
                    "relation": "product",
                    "on_path": True,
                }
            )

    return {
        "found": True,
        "hops": len(rxn_ids),
        "path_uids": path_uids,
        "nodes": nodes,
        "links": links,
    }


@router.get("/path")
def kg_find_path(
    start: str = Query(..., description="Start molecule SMILES or InChIKey"),
    end: str = Query(..., description="End molecule SMILES or InChIKey"),
    mode: str = Query("both", description="forward | retro | both"),
    max_depth: int = Query(8, ge=1, le=12),
):
    """Find the shortest reaction path (by hop count) between two molecules."""
    if mode not in ("forward", "retro", "both"):
        raise HTTPException(400, "mode must be forward, retro, or both")

    conn, is_pg = _get_connection(_db_path())
    try:
        start_row = _resolve_mol_id(conn, is_pg, start)
        if not start_row:
            raise HTTPException(404, f"Start molecule not found: {start!r}")
        end_row = _resolve_mol_id(conn, is_pg, end)
        if not end_row:
            raise HTTPException(404, f"End molecule not found: {end!r}")

        start_id, start_smiles, start_ik = start_row
        end_id, end_smiles, end_ik = end_row

        path = _bfs_path(start_id, end_id, mode=mode, max_depth=max_depth)
        if path is None:
            return {
                "found": False,
                "hops": None,
                "path_uids": [],
                "nodes": [
                    _molecule_node(start_id, start_smiles, start_ik),
                    _molecule_node(end_id, end_smiles, end_ik),
                ],
                "links": [],
                "message": f"No path found within {max_depth} steps (mode={mode}).",
            }

        return _path_to_graph(conn, is_pg, path, mode)
    finally:
        conn.close()
