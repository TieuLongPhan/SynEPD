"""High-level Python interface to a normalized SynEPD release."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

import networkx as nx

from synepd.core.data import get_default_db_path
from synepd.core.query import find_reactions_by_template, query_epd_by_reaction
from synepd.database import (
    ReleaseDatabase,
    ReleaseReactionXref,
    ReleaseTaxonXref,
    SQLiteReleaseRepository,
)
from synepd.database.managers import (
    EPDManager,
    MechanismManager,
    ReactionManager,
    TaxonomyManager,
)


@contextmanager
def _quiet_synreactor_logging():
    """Keep SynKit's per-mapping INFO messages out of high-level queries."""
    logger = logging.getLogger("synreactor")
    previous_level = logger.level
    logger.setLevel(logging.WARNING)
    try:
        yield
    finally:
        logger.setLevel(previous_level)


class SynEPDQuery:
    """Read-only, scenario-oriented interface to a SynEPD SQLite release.

    Parameters
    ----------
    db_path:
        Path to a normalized SynEPD release database. Use
        :meth:`from_release` to resolve a tagged remote release instead.
    """

    def __init__(self, db_path: str | Path = "data/epdb.sqlite"):
        self.db_path = Path(db_path)
        self._database = ReleaseDatabase(self.db_path, read_only=True)
        self._repository = SQLiteReleaseRepository(self.db_path)
        self._reactions = ReactionManager(self._database)
        self._taxonomy = TaxonomyManager(self._database)
        self._epd = EPDManager(self._database)
        self._mechanisms = MechanismManager(self._database)

    @classmethod
    def from_release(
        cls,
        *,
        version: str = "0.4.0",
        source: str = "auto",
        force: bool = False,
    ) -> "SynEPDQuery":
        """Resolve a cached tagged release and open its database read-only."""
        return cls(get_default_db_path(version=version, source=source, force=force))

    def close(self) -> None:
        """Close the query and typed-repository connections."""
        self._repository.close()
        self._database.close()

    def __enter__(self) -> "SynEPDQuery":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def reaction(self, case_id: str) -> dict[str, Any] | None:
        """Return one reaction by stable case ID."""
        return self._reactions.get_by_case_id(case_id)

    def search_reactions(self, text: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Search reaction names, case IDs, and reaction SMILES."""
        return self._reactions.search(text, limit=limit)

    def reactions_for_molecule(
        self, smiles: str, *, role: str = "both"
    ) -> list[dict[str, Any]]:
        """Return reactions containing a molecule on the requested side."""
        if role not in {"reactant", "product", "both"}:
            raise ValueError('role must be "reactant", "product", or "both"')
        return self._reactions.get_by_molecule(smiles, role=role)

    def reactions_in_taxon(self, taxon_code: str) -> list[dict[str, Any]]:
        """Return reactions assigned directly to a POLAR taxon."""
        return self._taxonomy.get_reactions_by_taxon(taxon_code)

    def reactions_matching_taxonomy(self, text: str) -> list[dict[str, Any]]:
        """Return reactions whose assigned taxonomy name contains ``text``."""
        return self._taxonomy.get_reactions_by_class_name(text)

    def taxonomy_path(self, taxon_code: str) -> list[dict[str, Any]]:
        """Return the root-to-node path for a POLAR taxon."""
        return self._taxonomy.get_hierarchy_path(taxon_code)

    def reactions_by_arrow_count(self, count: int) -> list[dict[str, Any]]:
        """Return reactions with exactly ``count`` EPD arrows."""
        return self._epd.get_reactions_by_arrow_count(count)

    def reactions_by_arrow_sequence(
        self, sequence: Iterable[str]
    ) -> list[dict[str, Any]]:
        """Return reactions with an exact ordered arrow-type sequence."""
        return self._epd.get_reactions_by_arrow_sequence(list(sequence))

    def reactions_containing_arrow(self, arrow_type: str) -> list[dict[str, Any]]:
        """Return reactions containing ``arrow_type`` at any step."""
        return self._epd.get_reactions_containing_arrow(arrow_type)

    def epd(self, reaction_smiles: str) -> dict[str, Any]:
        """Look up or product-verify an EPD projection for reaction SMILES."""
        with _quiet_synreactor_logging():
            return query_epd_by_reaction(reaction_smiles, db_path=self.db_path)

    def template_neighbors(
        self,
        *,
        case_id: str | None = None,
        template: str | nx.Graph | None = None,
    ) -> list[dict[str, Any]]:
        """Return reactions sharing a chemistry-aware RC template.

        Provide exactly one of ``case_id`` or a mapped-reaction/NetworkX
        ``template``.
        """
        if (case_id is None) == (template is None):
            raise ValueError("provide exactly one of case_id or template")
        if case_id is not None:
            reaction = self.reaction(case_id)
            if reaction is None:
                return []
            template = reaction["aam_key"]
        with _quiet_synreactor_logging():
            return find_reactions_by_template(template, db_path=self.db_path)

    def mechanism(self, case_id: str) -> dict[str, Any] | None:
        """Return a reaction with its arrows and decoded ITS, RC, and MC graphs."""
        reaction = self.reaction(case_id)
        if reaction is None:
            return None
        reaction_id = int(reaction["id"])
        return {
            "reaction": reaction,
            "arrows": self._repository.get_arrows(reaction_id),
            "its": self._mechanisms.get_its_for_reaction(reaction_id),
            "rc": self._mechanisms.get_reaction_center_for_reaction(reaction_id),
            "mc": self._mechanisms.get_mechanistic_center(reaction_id),
        }

    def reaction_xrefs(self, case_id: str) -> tuple[ReleaseReactionXref, ...]:
        """Return direct and inherited RXNO/MOP links for a reaction."""
        reaction = self.reaction(case_id)
        if reaction is None:
            return ()
        return self._repository.get_reaction_xrefs(int(reaction["id"]))

    def taxon_xrefs(self, taxon_code: str) -> tuple[ReleaseTaxonXref, ...]:
        """Return direct RXNO/MOP links curated for a POLAR taxon."""
        return self._repository.get_taxon_xrefs(taxon_code)
