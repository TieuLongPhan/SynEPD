"""
Example queries demonstrating the SynEPD API.
These managers allow you to query the database using Python objects natively,
completely hiding the underlying SQLite logic.
"""

from pathlib import Path
from synepd.database.models import SynEPDDatabase
from synepd.database.managers import (
    ReactionManager,
    MoleculeManager,
    TaxonomyManager,
    EPDManager,
    MechanismManager,
)


def run_queries():
    db_path = Path("release_v1.sqlite")
    if not db_path.exists():
        print("Database not found! Run test_verify_db.py or build_release_db.py first.")
        return

    with SynEPDDatabase(db_path) as db:

        # 1. Reaction Manager: Querying by smiles and molecule role
        reaction_mgr = ReactionManager(db)
        print("--- REACTION QUERIES ---")

        # Find all reactions where ammonium ([NH4+]) is a Reactant
        ethanol_reactions = reaction_mgr.get_by_molecule("[NH4+]", role="reactant")
        print(f"Reactions consuming Ammonium: {len(ethanol_reactions)}")

        # 2. Taxonomy Manager: Querying the hierarchy
        tax_mgr = TaxonomyManager(db)
        print("\n--- TAXONOMY QUERIES ---")

        # Fetch the entire classification path backwards to the root
        path = tax_mgr.get_hierarchy_path("POLAR.01.01")
        print(f"Classification Path for POLAR.01.01:")
        for node in path:
            print(f"  -> [{node['level']}] {node['code']}: {node['name']}")

        # 3. EPD Manager: Querying strictly by electron pushing mechanisms
        epd_mgr = EPDManager(db)
        print("\n--- ELECTRON PUSHING QUERIES ---")

        # Find reactions that are strictly a 2-arrow push sequence: LP-/Sigma+ followed by Pi-/LP+
        arrow_sequence = ["LP-/Sigma+", "Pi-/LP+"]
        mechanisms = epd_mgr.get_reactions_by_arrow_sequence(arrow_sequence)
        print(f"Reactions with sequence {arrow_sequence}: {len(mechanisms)}")

        # 4. Mechanism Manager: Fetching native NetworkX graphs
        mech_mgr = MechanismManager(db)
        print("\n--- GRAPH QUERIES ---")

        # Let's take the first reaction from the mechanism query (if any exist)
        if mechanisms:
            rxn = mechanisms[0]
            its_data = mech_mgr.get_its_for_reaction(rxn["id"])

            if its_data:
                # The 'graph_data' is instantly decompressed and returned as a NetworkX graph!
                nx_graph = its_data["graph_data"]
                nodes = nx_graph.number_of_nodes()
                edges = nx_graph.number_of_edges()
                print(
                    f"Loaded ITS graph for {rxn['case_id']}: {nodes} nodes, {edges} edges."
                )


if __name__ == "__main__":
    run_queries()
