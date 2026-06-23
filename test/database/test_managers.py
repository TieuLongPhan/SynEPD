import json
import pickle
import zlib

import networkx as nx
import pytest

from synepd.database.models import SynEPDDatabase
from synepd.database.managers import (
    EPDManager,
    MechanismManager,
    MoleculeManager,
    ReactionManager,
    TaxonomyManager,
)


@pytest.fixture()
def setup_db(tmp_path):
    db = SynEPDDatabase(tmp_path / "test_managers.sqlite")
    db.create_tables()
    db.init_vocabulary()

    graph = nx.Graph()
    graph.add_node(1, element="C", charge=0, atom_map=1)
    graph.add_node(2, element="O", charge=-1, atom_map=2)
    graph.add_edge(1, 2, order=1)
    graph_blob = zlib.compress(pickle.dumps(graph, protocol=pickle.HIGHEST_PROTOCOL))

    with db.connection:
        db.connection.executemany(
            "INSERT INTO taxon (code, parent_code, level, name) VALUES (?, ?, ?, ?)",
            [
                ("POLAR", None, 1, "Polar"),
                ("POLAR.01", "POLAR", 2, "Bond polarization"),
                ("POLAR.01.01", "POLAR.01", 3, "Transfer"),
                ("POLAR.01.01.001", "POLAR.01.01", 4, "Tiny methoxide example"),
            ],
        )
        cursor = db.connection.execute(
            "INSERT INTO reaction (case_id, canonical_rsmi, aam_key, name) VALUES (?, ?, ?, ?)",
            (
                "polar_000001",
                "C[O-]>>CO",
                "[CH3:1][O-:2]>>[CH3:1][O:2]",
                "Tiny methoxide example",
            ),
        )
        reaction_id = cursor.lastrowid
        db.connection.execute(
            "INSERT INTO molecule (canonical_smiles, inchikey) VALUES (?, ?)",
            ("C[O-]", "METHOXIDE-INCHIKEY"),
        )
        methoxide_id = db.connection.execute(
            "SELECT id FROM molecule WHERE canonical_smiles = ?", ("C[O-]",)
        ).fetchone()[0]
        db.connection.execute(
            "INSERT INTO molecule (canonical_smiles, inchikey) VALUES (?, ?)",
            ("CO", "METHANOL-INCHIKEY"),
        )
        methanol_id = db.connection.execute(
            "SELECT id FROM molecule WHERE canonical_smiles = ?", ("CO",)
        ).fetchone()[0]
        db.connection.execute(
            "INSERT INTO reaction_component (reaction_id, molecule_id, side, component_index) VALUES (?, ?, ?, ?)",
            (reaction_id, methoxide_id, "reactant", 1),
        )
        db.connection.execute(
            "INSERT INTO reaction_component (reaction_id, molecule_id, side, component_index) VALUES (?, ?, ?, ?)",
            (reaction_id, methanol_id, "product", 1),
        )
        db.connection.execute(
            "INSERT INTO reaction_taxonomy (reaction_id, taxon_code) VALUES (?, ?)",
            (reaction_id, "POLAR.01.01.001"),
        )
        cursor = db.connection.execute(
            "INSERT INTO reaction_center (wlhash, template_graph, graph_format) VALUES (?, ?, ?)",
            ("fake-wlhash", graph_blob, "pickle.gz"),
        )
        rc_id = cursor.lastrowid
        db.connection.execute(
            "INSERT INTO its (reaction_id, rc_id, wlhash, graph_data, graph_format) VALUES (?, ?, ?, ?, ?)",
            (reaction_id, rc_id, "fake-wlhash", graph_blob, "pickle.gz"),
        )
        db.connection.execute(
            "INSERT INTO epd (reaction_id, number_arrows) VALUES (?, ?)",
            (reaction_id, 2),
        )
        db.connection.execute(
            "INSERT INTO epd_arrow (reaction_id, arrow_index, arrow_type_code, source_atoms, target_atoms) VALUES (?, ?, ?, ?, ?)",
            (reaction_id, 1, "Sigma-/LP+", json.dumps([1]), json.dumps([2])),
        )
        db.connection.execute(
            "INSERT INTO epd_arrow (reaction_id, arrow_index, arrow_type_code, source_atoms, target_atoms) VALUES (?, ?, ?, ?, ?)",
            (reaction_id, 2, "LP-/Sigma+", json.dumps([2]), json.dumps([1])),
        )

    yield db
    db.close()


def test_reaction_manager(setup_db):
    manager = ReactionManager(setup_db)

    reaction = manager.get_by_case_id("polar_000001")

    assert reaction is not None
    assert reaction["case_id"] == "polar_000001"
    assert "canonical_rsmi" in reaction
    assert manager.get_by_aam_key(reaction["aam_key"])["id"] == reaction["id"]
    assert manager.get_by_rsmi(reaction["canonical_rsmi"]) is not None
    assert (
        manager.get_by_molecule("C[O-]", role="reactant")[0]["case_id"]
        == "polar_000001"
    )
    assert manager.get_by_case_id("invalid") is None


def test_taxonomy_manager(setup_db):
    manager = TaxonomyManager(setup_db)

    reactions = manager.get_reactions_by_taxon("POLAR.01.01.001")
    assert [reaction["case_id"] for reaction in reactions] == ["polar_000001"]

    children = manager.get_taxon_children("POLAR.01.01")
    assert [child["code"] for child in children] == ["POLAR.01.01.001"]

    rxns = manager.get_reactions_by_class_name("methoxide")
    assert [reaction["case_id"] for reaction in rxns] == ["polar_000001"]

    path = manager.get_hierarchy_path("POLAR.01.01.001")
    assert [node["code"] for node in path] == [
        "POLAR",
        "POLAR.01",
        "POLAR.01.01",
        "POLAR.01.01.001",
    ]


def test_epd_manager(setup_db):
    manager = EPDManager(setup_db)

    assert (
        manager.get_reactions_containing_arrow("Sigma-/LP+")[0]["case_id"]
        == "polar_000001"
    )
    assert (
        manager.get_reactions_by_first_arrow("Sigma-/LP+")[0]["case_id"]
        == "polar_000001"
    )
    assert manager.get_reactions_by_arrow_count(2)[0]["case_id"] == "polar_000001"
    assert (
        manager.get_reactions_by_arrow_sequence(["Sigma-/LP+", "LP-/Sigma+"])[0][
            "case_id"
        ]
        == "polar_000001"
    )


def test_molecule_manager(setup_db):
    manager = MoleculeManager(setup_db)

    assert manager.get_synthesis_reactions("CO")[0]["case_id"] == "polar_000001"
    assert manager.get_consumption_reactions("C[O-]")[0]["case_id"] == "polar_000001"

    mol_data = manager.get_by_smiles("C[O-]")
    assert mol_data is not None
    assert mol_data["inchikey"] == "METHOXIDE-INCHIKEY"


def test_mechanism_manager(setup_db):
    reaction_mgr = ReactionManager(setup_db)
    mech_mgr = MechanismManager(setup_db)

    reaction = reaction_mgr.get_by_case_id("polar_000001")
    its_data = mech_mgr.get_its_for_reaction(reaction["id"])

    assert its_data is not None
    assert type(its_data["graph_data"]).__name__ == "Graph"

    rc_data = mech_mgr.get_reaction_center(its_data["wlhash"])
    assert rc_data is not None
    assert type(rc_data["template_graph"]).__name__ == "Graph"
