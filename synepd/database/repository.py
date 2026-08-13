"""Typed, read-only access to a built SynEPD release database."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Protocol, runtime_checkable

import networkx as nx

from synepd.core.graph_codec import decode_graph
from synepd.linkage import load_rxno_linkage, resolve_lineage_xrefs


@dataclass(frozen=True)
class ReleaseReaction:
    id: int
    case_id: str
    canonical_rsmi: str
    aam_key: str
    canonical_aam_key: str | None
    name: str | None


@dataclass(frozen=True)
class ReleaseArrow:
    index: int
    arrow_type: str
    source_atoms: tuple[int, ...]
    target_atoms: tuple[int, ...]


@dataclass(frozen=True)
class ReleaseReactionEntryCode:
    taxon_code: str
    entry_code: str
    index: int
    is_primary: bool


@dataclass(frozen=True)
class ReleaseMechanismContext:
    reaction_id: int
    construction_version: str
    context_hash: str
    anchor_graph: nx.Graph
    events: tuple[dict[str, object], ...]
    diagnostics: dict[str, object]


@dataclass(frozen=True)
class ReleaseMechanisticCenter:
    reaction_id: int
    id: int
    rc_id: int
    wlhash: str
    template_graph: nx.Graph
    transition_edge_count: int
    rc_extension_edge_count: int
    transient_only_edge_count: int


@dataclass(frozen=True)
class ReleaseTaxonXref:
    taxon_code: str
    ontology_id: str
    relation: str
    match_type: str
    score: float
    ontology_name: str
    ontology_release_id: str


@dataclass(frozen=True)
class ReleaseReactionXref:
    assigned_taxon_code: str
    mapping_taxon_code: str
    inheritance_depth: int
    ontology_id: str
    relation: str
    match_type: str
    score: float
    ontology_name: str
    ontology_release_id: str

    @property
    def inherited(self) -> bool:
        return self.inheritance_depth > 0


@dataclass(frozen=True)
class ReleaseOntologyRelease:
    id: str
    ontology_iri: str
    version_iri: str
    data_version: str
    source_sha256: str
    license_iri: str


@runtime_checkable
class ReleaseRepository(Protocol):
    """Supported read boundary for release chemistry records."""

    def get_reaction(self, reaction_id: int) -> ReleaseReaction | None: ...

    def get_arrows(self, reaction_id: int) -> tuple[ReleaseArrow, ...]: ...

    def get_reaction_entry_codes(
        self, reaction_id: int
    ) -> tuple[ReleaseReactionEntryCode, ...]: ...

    def get_mechanism_context(
        self, reaction_id: int
    ) -> ReleaseMechanismContext | None: ...

    def get_mechanistic_center(
        self, reaction_id: int
    ) -> ReleaseMechanisticCenter | None: ...

    def get_taxon_xrefs(self, taxon_code: str) -> tuple[ReleaseTaxonXref, ...]: ...

    def get_reaction_xrefs(
        self, reaction_id: int
    ) -> tuple[ReleaseReactionXref, ...]: ...

    def get_ontology_releases(self) -> tuple[ReleaseOntologyRelease, ...]: ...


class SQLiteReleaseRepository:
    """Read-only SQLite implementation of :class:`ReleaseRepository`."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA query_only = ON")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "SQLiteReleaseRepository":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def get_reaction(self, reaction_id: int) -> ReleaseReaction | None:
        row = self.connection.execute(
            """
            SELECT id, case_id, canonical_rsmi, aam_key, canonical_aam_key, name
            FROM reaction WHERE id = ?
            """,
            (reaction_id,),
        ).fetchone()
        if row is None:
            return None
        return ReleaseReaction(
            id=int(row["id"]),
            case_id=str(row["case_id"]),
            canonical_rsmi=str(row["canonical_rsmi"]),
            aam_key=str(row["aam_key"]),
            canonical_aam_key=row["canonical_aam_key"],
            name=row["name"],
        )

    def get_arrows(self, reaction_id: int) -> tuple[ReleaseArrow, ...]:
        rows = self.connection.execute(
            """
            SELECT arrow_index, arrow_type_code, source_atoms, target_atoms
            FROM epd_arrow WHERE reaction_id = ? ORDER BY arrow_index
            """,
            (reaction_id,),
        ).fetchall()
        return tuple(
            ReleaseArrow(
                index=int(row["arrow_index"]),
                arrow_type=str(row["arrow_type_code"]),
                source_atoms=tuple(
                    int(value) for value in json.loads(row["source_atoms"])
                ),
                target_atoms=tuple(
                    int(value) for value in json.loads(row["target_atoms"])
                ),
            )
            for row in rows
        )

    def get_reaction_entry_codes(
        self, reaction_id: int
    ) -> tuple[ReleaseReactionEntryCode, ...]:
        rows = self.connection.execute(
            """
            SELECT taxon_code, entry_code, code_index, is_primary
            FROM reaction_entry_code
            WHERE reaction_id = ? ORDER BY code_index
            """,
            (reaction_id,),
        ).fetchall()
        return tuple(
            ReleaseReactionEntryCode(
                taxon_code=str(row["taxon_code"]),
                entry_code=str(row["entry_code"]),
                index=int(row["code_index"]),
                is_primary=bool(row["is_primary"]),
            )
            for row in rows
        )

    def get_mechanism_context(self, reaction_id: int) -> ReleaseMechanismContext | None:
        row = self.connection.execute(
            """
            SELECT reaction_id, construction_version, context_hash, anchor_graph,
                   graph_format, events_json, diagnostics_json
            FROM mechanism_context WHERE reaction_id = ?
            """,
            (reaction_id,),
        ).fetchone()
        if row is None:
            return None
        return ReleaseMechanismContext(
            reaction_id=int(row["reaction_id"]),
            construction_version=str(row["construction_version"]),
            context_hash=str(row["context_hash"]),
            anchor_graph=decode_graph(row["anchor_graph"], row["graph_format"]),
            events=tuple(json.loads(row["events_json"])),
            diagnostics=json.loads(row["diagnostics_json"]),
        )

    def get_mechanistic_center(
        self, reaction_id: int
    ) -> ReleaseMechanisticCenter | None:
        row = self.connection.execute(
            """
            SELECT its.reaction_id, mc.id, mc.rc_id, mc.wlhash,
                   mc.template_graph, mc.graph_format,
                   mc.transition_edge_count, mc.rc_extension_edge_count,
                   mc.transient_only_edge_count
            FROM its
            JOIN mechanistic_center mc ON mc.id = its.mc_id
            WHERE its.reaction_id = ?
            """,
            (reaction_id,),
        ).fetchone()
        if row is None:
            return None
        return ReleaseMechanisticCenter(
            reaction_id=int(row["reaction_id"]),
            id=int(row["id"]),
            rc_id=int(row["rc_id"]),
            wlhash=str(row["wlhash"]),
            template_graph=decode_graph(row["template_graph"], row["graph_format"]),
            transition_edge_count=int(row["transition_edge_count"]),
            rc_extension_edge_count=int(row["rc_extension_edge_count"]),
            transient_only_edge_count=int(row["transient_only_edge_count"]),
        )

    def get_taxon_xrefs(self, taxon_code: str) -> tuple[ReleaseTaxonXref, ...]:
        by_taxon, _ = load_rxno_linkage(self.path)
        rows = by_taxon.get(taxon_code, ())
        return tuple(
            ReleaseTaxonXref(
                taxon_code=taxon_code,
                ontology_id=str(row["ontology_id"]),
                relation=str(row["relation"]),
                match_type=str(row["match_type"]),
                score=float(row["score"]),
                ontology_name=str(row["name"]),
                ontology_release_id=str(row["ontology_release_id"]),
            )
            for row in rows
        )

    def get_reaction_xrefs(self, reaction_id: int) -> tuple[ReleaseReactionXref, ...]:
        lineage = self.connection.execute(
            """
            WITH RECURSIVE lineage(
                assigned_taxon_code, mapping_taxon_code, inheritance_depth
            ) AS (
                SELECT taxon_code, taxon_code, 0
                FROM reaction_taxonomy
                WHERE reaction_id = ?
                UNION ALL
                SELECT lineage.assigned_taxon_code, taxon.parent_code,
                       lineage.inheritance_depth + 1
                FROM lineage
                JOIN taxon ON taxon.code = lineage.mapping_taxon_code
                WHERE taxon.parent_code IS NOT NULL
            )
            SELECT DISTINCT assigned_taxon_code, mapping_taxon_code,
                            inheritance_depth
            FROM lineage
            ORDER BY inheritance_depth, assigned_taxon_code, mapping_taxon_code
            """,
            (reaction_id,),
        ).fetchall()
        rows = resolve_lineage_xrefs(
            ((str(row[0]), str(row[1]), int(row[2])) for row in lineage), self.path
        )
        return tuple(
            ReleaseReactionXref(
                assigned_taxon_code=str(row["assigned_taxon_code"]),
                mapping_taxon_code=str(row["mapping_taxon_code"]),
                inheritance_depth=int(row["inheritance_depth"]),
                ontology_id=str(row["ontology_id"]),
                relation=str(row["relation"]),
                match_type=str(row["match_type"]),
                score=float(row["score"]),
                ontology_name=str(row["name"]),
                ontology_release_id=str(row["ontology_release_id"]),
            )
            for row in rows
        )

    def get_ontology_releases(self) -> tuple[ReleaseOntologyRelease, ...]:
        _, release = load_rxno_linkage(self.path)
        rows = [release] if release else []
        return tuple(
            ReleaseOntologyRelease(
                id=str(row["id"]),
                ontology_iri=str(row["ontology_iri"]),
                version_iri=str(row["version_iri"]),
                data_version=str(row["data_version"]),
                source_sha256=str(row["source_sha256"]),
                license_iri=str(row["license_iri"]),
            )
            for row in rows
        )
