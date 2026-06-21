import zlib
import pickle
from typing import List, Dict, Any, Optional

from synepd.database.models import SynEPDDatabase


class ReactionManager:
    def __init__(self, db: SynEPDDatabase):
        self.db = db

    def get_by_case_id(self, case_id: str) -> Optional[Dict[str, Any]]:
        cursor = self.db.connection.cursor()
        cursor.execute("SELECT * FROM reaction WHERE case_id = ?", (case_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_by_aam_key(self, aam_key: str) -> Optional[Dict[str, Any]]:
        cursor = self.db.connection.cursor()
        cursor.execute("SELECT * FROM reaction WHERE aam_key = ?", (aam_key,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_by_rsmi(self, canonical_rsmi: str) -> Optional[Dict[str, Any]]:
        """Retrieve a reaction by its canonical (unmapped) reaction SMILES."""
        cursor = self.db.connection.cursor()
        cursor.execute(
            "SELECT * FROM reaction WHERE canonical_rsmi = ?", (canonical_rsmi,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_by_molecule(
        self, query_smiles: str, role: str = "both"
    ) -> List[Dict[str, Any]]:
        """
        Query reactions based on a molecule's SMILES and its role.
        Automatically checks both the exact SMILES and an explicit-H version
        (since mechanistic databases often keep participating hydrogens explicit).
        """
        from rdkit import Chem

        smiles_variants = [query_smiles]
        try:
            mol = Chem.MolFromSmiles(query_smiles)
            if mol:
                # Add explicit H to see if it matches mechanistic representations
                mol_h = Chem.AddHs(mol, explicitOnly=False)
                explicit_smiles = Chem.MolToSmiles(mol_h, canonical=True)
                if explicit_smiles not in smiles_variants:
                    smiles_variants.append(explicit_smiles)
        except Exception:
            pass

        cursor = self.db.connection.cursor()
        if role == "reactant":
            side_condition = "rc.side = 'reactant'"
        elif role == "product":
            side_condition = "rc.side = 'product'"
        else:
            side_condition = "(rc.side = 'reactant' OR rc.side = 'product')"

        placeholders = ",".join("?" for _ in smiles_variants)
        query = f"""
            SELECT DISTINCT r.*
            FROM reaction r
            JOIN reaction_component rc ON rc.reaction_id = r.id
            JOIN molecule m ON m.id = rc.molecule_id
            WHERE m.canonical_smiles IN ({placeholders}) AND {side_condition}
        """
        cursor.execute(query, tuple(smiles_variants))
        return [dict(row) for row in cursor.fetchall()]


class MoleculeManager:
    def __init__(self, db: SynEPDDatabase):
        self.db = db

    def get_by_smiles(self, canonical_smiles: str) -> Optional[Dict[str, Any]]:
        """Retrieve molecule data (including id and inchikey) from SMILES."""
        cursor = self.db.connection.cursor()
        cursor.execute(
            "SELECT * FROM molecule WHERE canonical_smiles = ?", (canonical_smiles,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_synthesis_reactions(self, canonical_smiles: str) -> List[Dict[str, Any]]:
        cursor = self.db.connection.cursor()
        query = """
            SELECT r.*
            FROM reaction r
            JOIN reaction_component rc ON rc.reaction_id = r.id
            JOIN molecule m ON m.id = rc.molecule_id
            WHERE m.canonical_smiles = ? AND rc.side = 'product'
        """
        cursor.execute(query, (canonical_smiles,))
        return [dict(row) for row in cursor.fetchall()]

    def get_consumption_reactions(self, canonical_smiles: str) -> List[Dict[str, Any]]:
        cursor = self.db.connection.cursor()
        query = """
            SELECT r.*
            FROM reaction r
            JOIN reaction_component rc ON rc.reaction_id = r.id
            JOIN molecule m ON m.id = rc.molecule_id
            WHERE m.canonical_smiles = ? AND rc.side = 'reactant'
        """
        cursor.execute(query, (canonical_smiles,))
        return [dict(row) for row in cursor.fetchall()]


class TaxonomyManager:
    def __init__(self, db: SynEPDDatabase):
        self.db = db

    def get_reactions_by_taxon(self, taxon_code: str) -> List[Dict[str, Any]]:
        cursor = self.db.connection.cursor()
        query = """
            SELECT r.*
            FROM reaction r
            JOIN reaction_taxonomy rt ON rt.reaction_id = r.id
            WHERE rt.taxon_code = ?
        """
        cursor.execute(query, (taxon_code,))
        return [dict(row) for row in cursor.fetchall()]

    def get_taxon_children(self, parent_code: str) -> List[Dict[str, Any]]:
        cursor = self.db.connection.cursor()
        cursor.execute("SELECT * FROM taxon WHERE parent_code = ?", (parent_code,))
        return [dict(row) for row in cursor.fetchall()]

    def get_hierarchy_path(self, taxon_code: str) -> List[Dict[str, Any]]:
        """Returns the full taxonomic path from root down to the given taxon_code."""
        cursor = self.db.connection.cursor()
        path = []
        current_code = taxon_code
        while current_code:
            cursor.execute("SELECT * FROM taxon WHERE code = ?", (current_code,))
            row = cursor.fetchone()
            if not row:
                break
            path.insert(0, dict(row))
            current_code = row["parent_code"]
        return path

    def get_reactions_by_class_name(self, class_name: str) -> List[Dict[str, Any]]:
        cursor = self.db.connection.cursor()
        # Allows fuzzy matching on the class name
        query = """
            SELECT r.*
            FROM reaction r
            JOIN reaction_taxonomy rt ON rt.reaction_id = r.id
            JOIN taxon t ON t.code = rt.taxon_code
            WHERE t.name LIKE ?
        """
        cursor.execute(query, (f"%{class_name}%",))
        return [dict(row) for row in cursor.fetchall()]


class EPDManager:
    def __init__(self, db: SynEPDDatabase):
        self.db = db

    def get_reactions_by_first_arrow(
        self, arrow_type_code: str
    ) -> List[Dict[str, Any]]:
        cursor = self.db.connection.cursor()
        query = """
            SELECT r.*
            FROM reaction r
            JOIN epd_arrow ea ON ea.reaction_id = r.id
            WHERE ea.arrow_index = 1 AND ea.arrow_type_code = ?
        """
        cursor.execute(query, (arrow_type_code,))
        return [dict(row) for row in cursor.fetchall()]

    def get_reactions_by_arrow_count(self, count: int) -> List[Dict[str, Any]]:
        cursor = self.db.connection.cursor()
        query = """
            SELECT r.*
            FROM reaction r
            JOIN epd e ON e.reaction_id = r.id
            WHERE e.number_arrows = ?
        """
        cursor.execute(query, (count,))
        return [dict(row) for row in cursor.fetchall()]

    def get_reactions_containing_arrow(
        self, arrow_type_code: str
    ) -> List[Dict[str, Any]]:
        cursor = self.db.connection.cursor()
        query = """
            SELECT DISTINCT r.*
            FROM reaction r
            JOIN epd_arrow ea ON ea.reaction_id = r.id
            WHERE ea.arrow_type_code = ?
        """
        cursor.execute(query, (arrow_type_code,))
        return [dict(row) for row in cursor.fetchall()]

    def get_reactions_by_arrow_sequence(
        self, sequence: List[str]
    ) -> List[Dict[str, Any]]:
        if not sequence:
            return []

        cursor = self.db.connection.cursor()

        # Build a dynamic query to check the exact sequence of arrows
        joins = []
        conditions = []
        params = []

        for i, arrow in enumerate(sequence):
            alias = f"ea{i}"
            if i == 0:
                joins.append(f"JOIN epd_arrow {alias} ON {alias}.reaction_id = r.id")
            else:
                joins.append(
                    f"JOIN epd_arrow {alias} ON {alias}.reaction_id = r.id AND {alias}.arrow_index = {i + 1}"
                )

            if i == 0:
                conditions.append(f"{alias}.arrow_index = 1")

            conditions.append(f"{alias}.arrow_type_code = ?")
            params.append(arrow)
            if i == len(sequence) - 1:
                break

        query = f"SELECT DISTINCT r.* FROM reaction r {' '.join(joins)} WHERE {' AND '.join(conditions)}"
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]


class MechanismManager:
    def __init__(self, db: SynEPDDatabase):
        self.db = db

    def get_reaction_center(self, wlhash: str) -> Optional[Dict[str, Any]]:
        """Retrieve reaction center graph data by wlhash."""
        cursor = self.db.connection.cursor()
        cursor.execute("SELECT * FROM reaction_center WHERE wlhash = ?", (wlhash,))
        row = cursor.fetchone()
        if not row:
            return None
        data = dict(row)
        if data.get("graph_format") == "pickle.gz" and data.get("template_graph"):
            data["template_graph"] = pickle.loads(
                zlib.decompress(data["template_graph"])
            )
        return data

    def get_its_for_reaction(self, reaction_id: int) -> Optional[Dict[str, Any]]:
        """Retrieve ITS graph data for a specific reaction."""
        cursor = self.db.connection.cursor()
        cursor.execute("SELECT * FROM its WHERE reaction_id = ?", (reaction_id,))
        row = cursor.fetchone()
        if not row:
            return None
        data = dict(row)
        if data.get("graph_format") == "pickle.gz" and data.get("graph_data"):
            data["graph_data"] = pickle.loads(zlib.decompress(data["graph_data"]))
        return data

    def predict_atom_map(self, unmapped_rsmi: str) -> list:
        from synkit.Synthesis.Reactor.syn_reactor import SynReactor
        from synkit.Chem.Reaction.standardize import Standardize

        try:
            canonical_input = Standardize().fit(unmapped_rsmi)
        except Exception:
            canonical_input = unmapped_rsmi

        parts = canonical_input.split(">>")
        if len(parts) != 2:
            return []
        r_input, p_input = parts

        # 1. Check if exact match exists in DB
        from synepd.database.managers import ReactionManager

        rxn_mgr = ReactionManager(self.db)
        exact_match = rxn_mgr.get_by_rsmi(canonical_input)
        if exact_match:
            return [
                {
                    "status": "exact_match",
                    "aam": exact_match["aam_key"],
                    "reaction_id": exact_match["id"],
                }
            ]

        # 2. Iterate over all RC templates
        cursor = self.db.connection.cursor()
        cursor.execute(
            "SELECT id, wlhash, template_graph, graph_format FROM reaction_center"
        )

        import pickle
        import zlib

        predictions = []
        for row in cursor.fetchall():
            rc_id, wlhash, raw_data, fmt = row
            if raw_data is None or fmt != "pickle.gz":
                continue

            try:
                rc_graph = pickle.loads(zlib.decompress(raw_data))
                reactor = SynReactor(
                    substrate=r_input,
                    template=rc_graph,
                    explicit_h=True,
                    implicit_temp=False,
                )
                smarts = reactor.smarts
                if smarts:
                    for generated_aam in smarts:
                        try:
                            gen_standard = Standardize().fit(generated_aam)
                            _, gen_p = gen_standard.split(">>")
                            # Verify the product structure matches exactly
                            if gen_p == p_input:
                                predictions.append(
                                    {
                                        "status": "template_match",
                                        "rc_id": rc_id,
                                        "wlhash": wlhash,
                                        "predicted_aam": generated_aam,
                                    }
                                )
                        except Exception:
                            continue
            except Exception:
                continue

        return predictions
