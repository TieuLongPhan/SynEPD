Querying Guide
==============

``SynEPDQuery`` is the recommended Python interface for SynEPD 0.4.1. It opens
the release read-only and provides scenario-oriented methods, so callers do not
need to know the SQLite schema or construct manager objects.

Quick Start
-----------

Use the database shipped with the repository:

.. code-block:: python

   from synepd.query import SynEPDQuery

   with SynEPDQuery("data/epdb.sqlite") as query:
       reaction = query.reaction("polar_001321")
       print(reaction["name"])

       named = query.search_reactions("Mitsunobu")
       print([reaction["name"] for reaction in named])

To cache and open the tagged v0.4.1 release, use ``auto``. It prefers a
configured Zenodo archive and falls back to the matching GitHub release:

.. code-block:: python

   from synepd.query import SynEPDQuery

   with SynEPDQuery.from_release(version="0.4.1", source="auto") as query:
       reaction = query.reaction("polar_001321")

Scenario 1: Query or Project an EPD
-----------------------------------

An exact database mechanism returns path 1. If no exact reaction exists,
SynEPD attempts a product-verified projection from a compatible RC/MC context
and returns path 2. Failures and genuine ambiguity are explicit.

.. code-block:: python

   with SynEPDQuery() as query:
       result = query.epd("CC(=O)Cl.CCO>>CC(=O)OCC")

       if not result["success"]:
           raise RuntimeError(result["error"])

       print("path:", result["path"])
       print("ambiguous:", result.get("mechanism_ambiguous", False))
       for arrow in result["arrows"]:
           print(
               arrow["arrow_index"],
               arrow["arrow_type_code"],
               arrow["source_atoms"],
               "->",
               arrow["target_atoms"],
           )

Scenario 2: Find Reactions Sharing an RC
----------------------------------------

Lookup by stable case ID avoids manually extracting or mapping a template.
Matches use chemistry-aware RC isomorphism rather than WL hash alone.

.. code-block:: python

   with SynEPDQuery() as query:
       neighbors = query.template_neighbors(case_id="polar_001321")
       print([reaction["case_id"] for reaction in neighbors])

A mapped reaction SMILES or NetworkX RC graph can instead be supplied through
``template=...``.

Scenario 3: Search by Molecule Role
-----------------------------------

``role`` can be ``reactant``, ``product``, or ``both``.

.. code-block:: python

   with SynEPDQuery() as query:
       ethanol_inputs = query.reactions_for_molecule("CCO", role="reactant")
       methanol_outputs = query.reactions_for_molecule("CO", role="product")
       print(len(ethanol_inputs), len(methanol_outputs))

Scenario 4: Search and Traverse the Taxonomy
--------------------------------------------

Use stable POLAR codes for exact assignment queries or text for broader class
name discovery.

.. code-block:: python

   with SynEPDQuery() as query:
       members = query.reactions_in_taxon("POLAR.04.01.012")
       path = query.taxonomy_path("POLAR.04.01.012")
       related = query.reactions_matching_taxonomy("acylation")

       print([node["name"] for node in path])
       print(len(members), len(related))

Scenario 5: Search Electron-Flow Grammar
----------------------------------------

Search by arrow count, arrow occurrence, or an exact ordered sequence.

.. code-block:: python

   with SynEPDQuery() as query:
       two_arrow = query.reactions_by_arrow_count(2)
       lp_to_sigma = query.reactions_containing_arrow("LP-/Sigma+")
       substitutions = query.reactions_by_arrow_sequence(
           ["LP-/Sigma+", "Sigma-/LP+"]
       )
       print(len(two_arrow), len(lp_to_sigma), len(substitutions))

Scenario 6: Compare ITS, RC, and MC Graphs
------------------------------------------

``mechanism`` follows the actual reaction→ITS→RC/MC links and returns decoded
NetworkX graphs plus typed arrows.

.. code-block:: python

   with SynEPDQuery() as query:
       mechanism = query.mechanism("polar_001321")

       print("arrows:", len(mechanism["arrows"]))
       print("ITS edges:", mechanism["its"]["graph_data"].number_of_edges())
       print("RC edges:", mechanism["rc"]["template_graph"].number_of_edges())
       print("MC edges:", mechanism["mc"]["template_graph"].number_of_edges())

Scenario 7: Resolve RXNO/MOP Links
----------------------------------

Reaction links include direct and inherited taxonomy mappings. Each result
preserves the assigned taxon, mapping-owning taxon, inheritance depth,
relation, and pinned ontology release.

.. code-block:: python

   with SynEPDQuery() as query:
       xrefs = query.reaction_xrefs("polar_001321")
       print(
           [
               (xref.ontology_id, xref.relation, xref.inheritance_depth)
               for xref in xrefs
           ]
       )

Advanced and Legacy Interfaces
------------------------------

The manager classes exported from ``synepd.query`` remain available for
specialized release queries. ``Query``, ``find_cases``, and ``search_labels``
remain available for the original JSON/JSONL case-index API. New code should
normally start with ``SynEPDQuery``.
