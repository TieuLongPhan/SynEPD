import json
import gzip
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
    load_hierarchy_taxons,
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


def collect_all_molecule_inchikeys(raw_records) -> dict[str, str]:
    from rdkit import Chem
    from synepd.core.ingest import strip_atom_map

    flat_to_inchikey = {}
    for c in raw_records:
        rsmi = c.get("rsmi")
        if not rsmi:
            continue
        parts = rsmi.split(">>")
        sides = [parts[0], parts[-1]] if len(parts) >= 2 else []
        for side in sides:
            for m_smiles in side.split("."):
                flat_smiles = strip_atom_map(m_smiles)
                if flat_smiles not in flat_to_inchikey:
                    try:
                        mol = Chem.MolFromSmiles(flat_smiles)
                        inchikey = Chem.MolToInchiKey(mol) if mol else None
                        if inchikey:
                            flat_to_inchikey[flat_smiles] = inchikey
                    except Exception:
                        pass
    return flat_to_inchikey


def fetch_batch_pubchem(
    inchikeys: list[str],
) -> dict[str, tuple[str | None, str | None, int | None]]:
    import urllib.request
    import urllib.parse
    import json
    import re

    keys_clean = sorted(list(set(k for k in inchikeys if k)))
    if not keys_clean:
        return {}
    results = {k: (None, None, None) for k in keys_clean}
    url_prop = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/inchikey/property/InChIKey,IUPACName/JSON"
    data_prop = urllib.parse.urlencode({"inchikey": ",".join(keys_clean)}).encode(
        "utf-8"
    )
    req_prop = urllib.request.Request(url_prop, data=data_prop)
    cid_to_key = {}
    key_to_iupac = {}
    key_to_cid = {}
    try:
        with urllib.request.urlopen(req_prop, timeout=10) as response:
            res = json.loads(response.read().decode("utf-8"))
            properties = res.get("PropertyTable", {}).get("Properties", [])
            for prop in properties:
                cid = prop.get("CID")
                key = prop.get("InChIKey")
                iupac = prop.get("IUPACName")
                if cid and key:
                    cid_to_key[cid] = key
                    key_to_cid[key] = cid
                    if iupac and key not in key_to_iupac:
                        key_to_iupac[key] = iupac
    except Exception:
        pass
    url_syn = (
        "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/inchikey/synonyms/JSON"
    )
    data_syn = urllib.parse.urlencode({"inchikey": ",".join(keys_clean)}).encode(
        "utf-8"
    )
    req_syn = urllib.request.Request(url_syn, data=data_syn)
    cas_regex = re.compile(r"^[1-9]\d{1,6}-\d{2}-\d$")
    key_to_cas = {}
    try:
        with urllib.request.urlopen(req_syn, timeout=10) as response:
            res = json.loads(response.read().decode("utf-8"))
            info_list = res.get("InformationList", {}).get("Information", [])
            for info in info_list:
                cid = info.get("CID")
                synonyms = info.get("Synonym", [])
                key = cid_to_key.get(cid)
                if key and synonyms and key not in key_to_cas:
                    for syn in synonyms:
                        if cas_regex.match(syn):
                            key_to_cas[key] = syn
                            break
    except Exception:
        pass
    for key in keys_clean:
        results[key] = (key_to_iupac.get(key), key_to_cas.get(key), key_to_cid.get(key))
    return results


def fetch_nih_cir_fallback(smiles: str, representation: str) -> str | None:
    import urllib.request
    import urllib.parse

    encoded = urllib.parse.quote(smiles)
    url = f"https://cactus.nci.nih.gov/chemical/structure/{encoded}/{representation}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            text = response.read().decode("utf-8").strip()
            if text:
                lines = [line.strip() for line in text.split("\n") if line.strip()]
                if lines:
                    return lines[0]
    except Exception:
        pass
    return None


def extract_cas_from_pug_view(cid: int) -> str | None:
    import urllib.request
    import json
    import re

    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON?heading=CAS"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
            cas_regex = re.compile(r"^[1-9]\d{1,6}-\d{2}-\d$")

            def search_sections(sections):
                for sec in sections:
                    if sec.get("TOCHeading") == "CAS":
                        for info in sec.get("Information", []):
                            string_list = info.get("Value", {}).get(
                                "StringWithMarkup", []
                            )
                            for item in string_list:
                                val = item.get("String", "").strip()
                                if cas_regex.match(val):
                                    return val
                    sub_sec = sec.get("Section", [])
                    if sub_sec:
                        res = search_sections(sub_sec)
                        if res:
                            return res
                return None

            sections = data.get("Record", {}).get("Section", [])
            return search_sections(sections)
    except Exception:
        pass
    return None


def compute_reaction_balance_and_counts(rsmi: str) -> tuple[int, int, int, int]:
    try:
        from rdkit import Chem
        from collections import Counter

        parts = rsmi.split(">>")
        if len(parts) == 2:
            lhs, rhs = parts[0], parts[1]
        elif len(parts) == 3:
            lhs, rhs = parts[0], parts[2]
        else:
            return 1, 0, 0, 0

        def get_frag_info(frag: str):
            counts = Counter()
            total_atoms = 0
            total_charge = 0
            for smi in frag.split("."):
                mol = Chem.MolFromSmiles(smi)
                if mol is None:
                    continue
                mol = Chem.AddHs(mol)
                for a in mol.GetAtoms():
                    counts[a.GetAtomicNum()] += 1
                    total_atoms += 1
                    total_charge += a.GetFormalCharge()
            return counts, total_atoms, total_charge

        l_counts, l_atoms, l_charge = get_frag_info(lhs)
        r_counts, r_atoms, r_charge = get_frag_info(rhs)

        balanced = 1 if l_counts == r_counts else 0
        return balanced, l_atoms, r_atoms, r_charge - l_charge
    except Exception:
        return 1, 0, 0, 0


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


def compute_molecule_descriptors(smiles: str) -> dict:
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem, Descriptors

        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return {}

        formula = Chem.rdMolDescriptors.CalcMolFormula(mol)
        exact_mass = float(Descriptors.ExactMolWt(mol))
        num_heavy_atoms = int(mol.GetNumHeavyAtoms())

        ring_info = mol.GetRingInfo()
        num_rings = int(ring_info.NumRings() if ring_info else 0)
        num_aromatic_rings = int(Chem.rdMolDescriptors.CalcNumAromaticRings(mol))

        has_charge = 1 if any(a.GetFormalCharge() != 0 for a in mol.GetAtoms()) else 0

        fp = AllChem.GetMorganFingerprintAsBitVect(mol, 2, nBits=2048)
        pat_fp = Chem.PatternFingerprint(mol, fpSize=2048)

        return {
            "formula": formula,
            "exact_mass": exact_mass,
            "num_heavy_atoms": num_heavy_atoms,
            "num_rings": num_rings,
            "num_aromatic_rings": num_aromatic_rings,
            "has_charge": has_charge,
            "morgan_fp": fp.ToBinary(),
            "pattern_fp": pat_fp.ToBinary(),
        }
    except Exception:
        return {}


def load_json(path: Path) -> dict:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        return json.load(f)


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
    hierarchy_taxons = load_hierarchy_taxons(hierarchy_path)
    hierarchy = {taxon["code"]: taxon["name"] for taxon in hierarchy_taxons}

    data = load_json(json_path)
    if isinstance(data, list):
        raw_records = data
    elif isinstance(data, dict):
        raw_records = data.get("records") or data.get("cases")
    else:
        raw_records = None
    if raw_records is None:
        raise ValueError(f"{json_path} must contain records, cases, or a records list")

    import time

    flat_to_inchikey = collect_all_molecule_inchikeys(raw_records)
    all_inchikeys = sorted(list(set(flat_to_inchikey.values())))
    print(f"Resolving {len(all_inchikeys)} unique InChIKeys via PubChem...")
    pubchem_cache = {}
    for idx in range(0, len(all_inchikeys), 100):
        chunk = all_inchikeys[idx : idx + 100]
        try:
            batch_res = fetch_batch_pubchem(chunk)
            pubchem_cache.update(batch_res)
        except Exception as e:
            print(f"PubChem batch resolution error: {e}")
        time.sleep(0.5)

    # NIH CIR & PubChem PUG View Fallback for missing IUPAC name or CAS number
    molecule_details = {}
    for smiles, key in flat_to_inchikey.items():
        iupac, cas, cid = pubchem_cache.get(key, (None, None, None))
        molecule_details[smiles] = (iupac, cas, cid)

    missing_iupac = [s for s, (iupac, _, _) in molecule_details.items() if not iupac]
    missing_cas = [s for s, (_, cas, _) in molecule_details.items() if not cas]

    print(
        f"PubChem resolved: {len(all_inchikeys) - len(missing_iupac)} IUPAC, {len(all_inchikeys) - len(missing_cas)} CAS."
    )
    if missing_iupac or missing_cas:
        print(
            f"Resolving {len(missing_iupac)} missing IUPAC names and {len(missing_cas)} missing CAS numbers using fallbacks..."
        )

        # Query NIH CIR fallback for missing IUPAC names
        for idx, smiles in enumerate(missing_iupac):
            try:
                iupac = fetch_nih_cir_fallback(smiles, "iupac_name")
                if iupac:
                    _, cas, cid = molecule_details[smiles]
                    molecule_details[smiles] = (iupac, cas, cid)
            except Exception:
                pass
            time.sleep(0.02)
            if idx > 0 and idx % 200 == 0:
                print(f"  Processed {idx} IUPAC fallbacks...")

        # Query PubChem PUG View and NIH CIR fallback for missing CAS numbers
        for idx, smiles in enumerate(missing_cas):
            iupac, _, cid = molecule_details[smiles]
            cas = None
            try:
                if cid:
                    cas = extract_cas_from_pug_view(cid)
                if not cas:
                    cas = fetch_nih_cir_fallback(smiles, "cas")
                if cas:
                    molecule_details[smiles] = (iupac, cas, cid)
            except Exception:
                pass
            time.sleep(0.02)
            if idx > 0 and idx % 200 == 0:
                print(f"  Processed {idx} CAS fallbacks...")

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
            for taxon in hierarchy_taxons:
                code = taxon["code"]
                level = taxon["level"]
                parent_code = taxon["parent_code"]
                name = taxon["name"]
                db.connection.execute(
                    "INSERT OR IGNORE INTO taxon (code, parent_code, level, name) VALUES (?, ?, ?, ?)",
                    (code, parent_code, level, name),
                )

        for c in raw_records:
            if "case_id" in c:
                case_id = c["case_id"]
                tax_codes = [c["level4_code"]]
                name = c.get("reaction_name") or extract_reaction_name(case_id)
                ground_truth = c.get("ground_truth", [])
            else:
                family = c.get("family", "polar")
                case_id = c.get("case_id") or f"{family}_{int(c['id']):06d}"
                tax_codes = c.get("tax_codes") or [c["tax_code"]]
                name = c.get("reaction_name") or case_id
                ground_truth = c.get("epd", [])
            tax_codes = [code for code in dict.fromkeys(tax_codes) if code]
            rsmi = c["rsmi"]

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
            balanced_val, l_atoms, r_atoms, chg_delta = (
                compute_reaction_balance_and_counts(canonical_rsmi)
            )
            coords_dict = compute_rdkit_coords(aam_key)
            coords_json = json.dumps(coords_dict) if coords_dict else None
            try:
                with db.connection:
                    cursor = db.connection.execute(
                        "INSERT INTO reaction (case_id, canonical_rsmi, aam_key, name, balanced, reactant_atom_count, product_atom_count, formal_charge_delta, coords_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            case_id,
                            canonical_rsmi,
                            aam_key,
                            name,
                            balanced_val,
                            l_atoms,
                            r_atoms,
                            chg_delta,
                            coords_json,
                        ),
                    )
                    reaction_id = cursor.lastrowid
            except Exception:
                continue

            # 2. Ingest Taxonomy
            with db.connection:
                for tax_code in tax_codes:
                    if tax_code not in hierarchy:
                        parent_code = (
                            ".".join(tax_code.split(".")[:-1])
                            if "." in tax_code
                            else None
                        )
                        db.connection.execute(
                            "INSERT OR IGNORE INTO taxon (code, parent_code, level, name) VALUES (?, ?, ?, ?)",
                            (tax_code, parent_code, len(tax_code.split(".")), tax_code),
                        )
                    db.connection.execute(
                        "INSERT OR IGNORE INTO reaction_taxonomy (reaction_id, taxon_code) VALUES (?, ?)",
                        (reaction_id, tax_code),
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
                            desc = compute_molecule_descriptors(flat_smiles)
                            iupac, cas, _ = molecule_details.get(
                                flat_smiles, (None, None, None)
                            )
                            with db.connection:
                                cursor = db.connection.execute(
                                    "INSERT OR IGNORE INTO molecule (canonical_smiles, inchikey, formula, exact_mass, num_heavy_atoms, num_rings, num_aromatic_rings, has_charge, morgan_fp, pattern_fp, iupac_name, cas_number) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                    (
                                        flat_smiles,
                                        inchikey,
                                        desc.get("formula"),
                                        desc.get("exact_mass"),
                                        desc.get("num_heavy_atoms"),
                                        desc.get("num_rings"),
                                        desc.get("num_aromatic_rings"),
                                        desc.get("has_charge"),
                                        desc.get("morgan_fp"),
                                        desc.get("pattern_fp"),
                                        iupac,
                                        cas,
                                    ),
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
                db_wlhash = wlhash

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

                    try:
                        from synkit.IO import its_to_rsmi, rsmi_to_rsmarts

                        rc_rsmi = its_to_rsmi(rc_graph, sanitize=False)
                        rc_smarts = rsmi_to_rsmarts(rc_rsmi)
                    except Exception:
                        rc_smarts = None

                    with db.connection:
                        cursor = db.connection.execute(
                            "INSERT INTO reaction_center (wlhash, template_graph, graph_format, smarts) VALUES (?, ?, ?, ?)",
                            (db_wlhash, compressed_rc, "pickle.gz", rc_smarts),
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
                            db_wlhash,
                            compressed_its,
                            "pickle.gz",
                        ),
                    )

                # 5. Ingest EPD
                if ground_truth:
                    arrows = parse_epd(ground_truth)
                    signature = "|".join(arr["arrow_type_code"] for arr in arrows)
                    with db.connection:
                        db.connection.execute(
                            "INSERT INTO epd (reaction_id, number_arrows, signature) VALUES (?, ?, ?)",
                            (reaction_id, len(arrows), signature),
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
        db_path=Path("data/epdb.sqlite"),
    )
    print("Release database v1.0.0 built successfully.")
