import tempfile
from pathlib import Path
import pytest

from synepd.database.models import SynEPDDatabase
from synepd.database.managers import (
    ReactionManager,
    TaxonomyManager,
    EPDManager,
    MoleculeManager,
    MechanismManager,
)
from synepd.construct.build_release_db import build_release_database


@pytest.fixture(scope="module")
def setup_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_managers.sqlite"
        build_release_database(
            json_path=Path("data/polar.json"),
            hierarchy_path=Path("data/hierarchy.md"),
            db_path=db_path,
        )

        db = SynEPDDatabase(db_path)
        yield db
        db.close()


def test_reaction_manager(setup_db):
    db = setup_db
    manager = ReactionManager(db)

    # We know polar01_001_alcohol_protonation_deprotonation exists
    case_id = "polar01_001_alcohol_protonation_deprotonation"
    reaction = manager.get_by_case_id(case_id)

    assert reaction is not None
    assert reaction["case_id"] == case_id
    assert "canonical_rsmi" in reaction

    # Test by aam_key
    aam_key = reaction["aam_key"]
    reaction_by_aam = manager.get_by_aam_key(aam_key)

    assert reaction_by_aam is not None
    assert reaction_by_aam["id"] == reaction["id"]

    # Test new query APIs
    assert manager.get_by_rsmi(reaction["canonical_rsmi"]) is not None

    # Test molecule role queries (ammonium is a reactant in polar01_001)
    nh4_rxns = manager.get_by_molecule("[NH4+]", role="reactant")
    assert len(nh4_rxns) > 0
    assert any(r["case_id"] == case_id for r in nh4_rxns)

    # Non-existent
    assert manager.get_by_case_id("invalid") is None


def test_taxonomy_manager(setup_db):
    db = setup_db
    manager = TaxonomyManager(db)

    # polar_1 has POLAR.01.01.001
    reactions = manager.get_reactions_by_taxon("POLAR.01.01.001")
    assert len(reactions) > 0
    assert reactions[0]["case_id"] == "polar01_001_alcohol_protonation_deprotonation"

    # Test children
    children = manager.get_taxon_children("POLAR.01.01")
    assert len(children) > 0
    assert any(c["code"] == "POLAR.01.01.001" for c in children)

    # Test by class name
    rxns = manager.get_reactions_by_class_name("Alcohol protonation")
    assert len(rxns) > 0
    assert any(
        r["case_id"] == "polar01_001_alcohol_protonation_deprotonation" for r in rxns
    )

    # Test hierarchy path extraction
    path = manager.get_hierarchy_path("POLAR.01.01.001")
    assert len(path) == 4
    assert path[0]["code"] == "POLAR"
    assert path[-1]["code"] == "POLAR.01.01.001"


def test_epd_manager(setup_db):
    db = setup_db
    manager = EPDManager(db)

    # EPD arrows for this reaction typically have Sigma-/LP+
    rxns_with_sigma_lp = manager.get_reactions_containing_arrow("Sigma-/LP+")
    assert len(rxns_with_sigma_lp) > 0

    # First arrow testing
    rxns_first = manager.get_reactions_by_first_arrow("Sigma-/LP+")
    assert len(rxns_first) > 0

    # Count testing (the demo case usually has 2 arrows)
    rxns_count_2 = manager.get_reactions_by_arrow_count(2)
    assert len(rxns_count_2) > 0

    # Exact sequence testing
    rxns_seq = manager.get_reactions_by_arrow_sequence(["Sigma-/LP+", "LP-/Sigma+"])
    # Not all reactions have this exact sequence, but the logic should run successfully
    assert isinstance(rxns_seq, list)


def test_molecule_manager(setup_db):
    db = setup_db
    manager = MoleculeManager(db)

    # Find synthesis routes
    # Methanol (or similar) is likely a product in some reactions
    # Let's just ensure it executes and returns a list
    synth_rxns = manager.get_synthesis_reactions("CO")
    assert isinstance(synth_rxns, list)

    # Find consumption routes
    # Ammonia (N) or water (O)
    cons_rxns = manager.get_consumption_reactions("N")
    assert isinstance(cons_rxns, list)

    # Test exact fetching with inchikey
    mol_data = manager.get_by_smiles("C[O-]")
    assert mol_data is not None
    assert "inchikey" in mol_data


def test_mechanism_manager(setup_db):
    db = setup_db
    reaction_mgr = ReactionManager(db)
    mech_mgr = MechanismManager(db)

    reaction = reaction_mgr.get_by_case_id(
        "polar01_001_alcohol_protonation_deprotonation"
    )
    assert reaction is not None

    its_data = mech_mgr.get_its_for_reaction(reaction["id"])
    assert its_data is not None
    assert "graph_data" in its_data
    assert type(its_data["graph_data"]).__name__ == "Graph"

    rc_data = mech_mgr.get_reaction_center(its_data["wlhash"])
    assert rc_data is not None
    assert "template_graph" in rc_data
