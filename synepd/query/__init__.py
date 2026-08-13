"""Public query API for case indexes and normalized SynEPD releases.

The manager classes query known release records, molecules, taxonomy, EPD
sequences, and graph representations.  The two high-level functions either
look up/project an EPD from reaction SMILES or find reactions sharing an RC
template.  The legacy :class:`Query` helpers remain available for JSON/JSONL
case indexes.
"""

from synepd.core.query import find_reactions_by_template, query_epd_by_reaction
from synepd.database.managers import (
    EPDManager,
    MechanismManager,
    MoleculeManager,
    ReactionManager,
    TaxonomyManager,
)

from synepd.query.filters import (
    Query,
    by_level,
    by_template_pool,
    find_cases,
    search_labels,
)
from synepd.query.release import SynEPDQuery

__all__ = [
    "Query",
    "EPDManager",
    "MechanismManager",
    "MoleculeManager",
    "ReactionManager",
    "TaxonomyManager",
    "SynEPDQuery",
    "by_level",
    "by_template_pool",
    "find_cases",
    "find_reactions_by_template",
    "query_epd_by_reaction",
    "search_labels",
]
