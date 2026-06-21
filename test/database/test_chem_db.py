import tempfile
from pathlib import Path
from synepd.database.chem_db import ChemistryDatabase


def test_chemistry_database_creation_and_insertion():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "chem.sqlite"

        with ChemistryDatabase(db_path) as db:
            db.create_tables()

            # 1. Insert Molecule
            mol_id = db.insert_molecule()
            assert mol_id == 1

            # 2. Insert MoleculeStructure
            struct_id = db.insert_molecule_structure(
                molecule_id=mol_id,
                signature=[1, 2, 3],
                fingerprint=[0, 1, 0, 1],
                structure=b"molecule_binary_representation",
            )
            assert struct_id == 1

            # 3. Insert NonOrganic
            non_org_id = db.insert_non_organic(b"non_organic_binary_data")
            assert non_org_id == 1

            # 4. Insert Substance
            sub_id = db.insert_substance()
            assert sub_id == 1

            # 5. Insert SubstanceStructure (organic component)
            sub_struct_org_id = db.insert_substance_structure(
                substance_id=sub_id,
                molar_fraction=0.7,
                molecule_id=mol_id,
                mapping={"atom_1": "atom_2"},
            )
            assert sub_struct_org_id == 1

            # 6. Insert SubstanceStructure (non-organic component)
            sub_struct_non_org_id = db.insert_substance_structure(
                substance_id=sub_id, molar_fraction=0.3, non_organic_id=non_org_id
            )
            assert sub_struct_non_org_id == 2

            # 7. Insert Reaction
            rxn_id = db.insert_reaction()
            assert rxn_id == 1

            # 8. Link Substance to Reaction
            link_id = db.insert_reaction_substance(
                reaction_id=rxn_id,
                substance_id=sub_id,
                mapping={"sub_atom": "rxn_atom"},
                is_product=True,
            )
            assert link_id == 1

            # 9. Insert CGR
            cgr_id = db.insert_cgr(
                reaction_id=rxn_id,
                fingerprint=[1, 1, 0, 0],
                structure=b"cgr_binary_representation",
            )
            assert cgr_id == 1

            # Verification queries
            cursor = db.connection.cursor()

            cursor.execute("SELECT * FROM MoleculeStructure WHERE id = ?", (struct_id,))
            row = cursor.fetchone()
            assert row["molecule_id"] == mol_id
            assert row["signature"] == "[1, 2, 3]"
            assert row["structure"] == b"molecule_binary_representation"

            cursor.execute("SELECT * FROM CGR WHERE reaction_id = ?", (rxn_id,))
            cgr_row = cursor.fetchone()
            assert cgr_row["id"] == cgr_id
            assert cgr_row["structure"] == b"cgr_binary_representation"
