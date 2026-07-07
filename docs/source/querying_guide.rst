Querying Guide
==============

This guide provides examples and explanations on how to query the SynEPD database using the high-level Python API.

Connecting to the Database
--------------------------
To start querying, initialize the `SynEPDDatabase` manager with the path to the database file:

.. code-block:: python

    from pathlib import Path
    from synepd.database.models import SynEPDDatabase

    db_path = Path("data/epdb.sqlite")
    db = SynEPDDatabase(db_path)

The published 0.1.0 database can also be resolved from the Zenodo release
archive and cached locally:

.. code-block:: python

    from synepd.core import get_default_db_path
    from synepd.database.models import SynEPDDatabase

    db_path = get_default_db_path(version="0.1.0", source="zenodo")
    db = SynEPDDatabase(db_path)

Use ``source="github"`` to resolve the same database from the matching GitHub
release tag archive.

Querying Reactions
------------------
Use `ReactionManager` to search and retrieve reaction records:

.. code-block:: python

    from synepd.database.managers import ReactionManager

    rxn_manager = ReactionManager(db)

    # 1. Retrieve a reaction by its unique Case ID
    reaction = rxn_manager.get_by_case_id("polar01_001_alcohol_protonation_deprotonation")
    if reaction:
        print("Reaction Name:", reaction["name"])
        print("Reaction SMILES:", reaction["canonical_rsmi"])

    # 2. Find all reactions involving a specific molecule SMILES (as reactant)
    reactions = rxn_manager.get_by_molecule("CCO", role="reactant")
    print(f"Found {len(reactions)} reactions consuming ethanol.")

Querying Molecules
------------------
Use `MoleculeManager` to get molecular properties (like IUPAC name, CAS number, formula, exact mass) and find where they are consumed or synthesized:

.. code-block:: python

    from synepd.database.managers import MoleculeManager

    mol_manager = MoleculeManager(db)

    # 1. Retrieve molecule details by SMILES
    molecule = mol_manager.get_by_smiles("CCO")
    if molecule:
        print("IUPAC Name:", molecule["iupac_name"])
        print("CAS Number:", molecule["cas_number"])
        print("Formula:", molecule["formula"])
        print("Exact Mass:", molecule["exact_mass"])

    # 2. Find reactions where a molecule is synthesized
    synth_rxns = mol_manager.get_synthesis_reactions("CC=O")
    print(f"Found {len(synth_rxns)} reactions producing acetaldehyde.")

Querying Taxonomy
-----------------
Use `TaxonomyManager` to filter reactions by mechanistic class or taxonomy:

.. code-block:: python

    from synepd.database.managers import TaxonomyManager

    tax_manager = TaxonomyManager(db)

    # 1. Retrieve all reactions belonging to a specific taxon code
    reactions = tax_manager.get_reactions_by_taxon("POLAR.04.01")
    print(f"Found {len(reactions)} reactions in taxon POLAR.04.01.")

    # 2. Search reactions by mechanistic class name (fuzzy match)
    reactions_by_name = tax_manager.get_reactions_by_class_name("Protonation")
    print(f"Found {len(reactions_by_name)} protonation reactions.")

Querying EPD (Electron Push Diagrams)
--------------------------------------
Use `EPDManager` to query reactions based on electron-push arrow counts and types:

.. code-block:: python

    from synepd.database.managers import EPDManager

    epd_manager = EPDManager(db)

    # 1. Retrieve reactions matching a specific arrow count
    reactions = epd_manager.get_reactions_by_arrow_count(2)
    print(f"Found {len(reactions)} 2-arrow elementary reactions.")

    # 2. Find reactions starting with a specific electron transfer (e.g. Lone Pair to Sigma Star)
    reactions_by_arrow = epd_manager.get_reactions_by_first_arrow("LP-/Sigma+")
    print(f"Found {len(reactions_by_arrow)} reactions initiated by LP to Sigma transfer.")

For the high-level EPD helper, the release source and version can be provided
directly:

.. code-block:: python

    from synepd.core import query_epd_by_reaction

    result = query_epd_by_reaction(
        "CC[O-].[NH4+]>>CCO",
        db_source="zenodo",
        db_version="0.1.0",
    )

Closing the Connection
----------------------
Always make sure to close the database connection when finished:

.. code-block:: python

    db.close()

Alternatively, you can use the database connection as a context manager:

.. code-block:: python

    with SynEPDDatabase("data/epdb.sqlite") as db:
        rxn_manager = ReactionManager(db)
        # Querying logic here...
