``synepd.query`` package
========================

The public query namespace combines the high-level v0.4 Python interface,
standalone reaction-SMILES/RC searches, release managers, and legacy case
filters. See :doc:`querying_guide` for complete scenarios.

Release query API
-----------------

.. autoclass:: synepd.query.SynEPDQuery
   :members:

.. autofunction:: synepd.query.query_epd_by_reaction

.. autofunction:: synepd.query.find_reactions_by_template

Advanced manager API
--------------------

.. autoclass:: synepd.query.ReactionManager
   :members:

.. autoclass:: synepd.query.MoleculeManager
   :members:

.. autoclass:: synepd.query.TaxonomyManager
   :members:

.. autoclass:: synepd.query.EPDManager
   :members:

.. autoclass:: synepd.query.MechanismManager
   :members:

Legacy case-filter API
----------------------

synepd.query.filters module
---------------------------

.. automodule:: synepd.query.filters
   :members:
   :show-inheritance:
   :undoc-members:
