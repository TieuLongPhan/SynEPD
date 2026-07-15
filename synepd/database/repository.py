"""Typed, read-only access to a built SynEPD release database."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Protocol, runtime_checkable

import networkx as nx

from synepd.core.graph_codec import decode_graph


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
class ReleaseMechanismContext:
    reaction_id: int
    construction_version: str
    context_hash: str
    anchor_graph: nx.Graph
    events: tuple[dict[str, object], ...]
    diagnostics: dict[str, object]


@runtime_checkable
class ReleaseRepository(Protocol):
    """Supported read boundary for release chemistry records."""

    def get_reaction(self, reaction_id: int) -> ReleaseReaction | None: ...

    def get_arrows(self, reaction_id: int) -> tuple[ReleaseArrow, ...]: ...

    def get_mechanism_context(
        self, reaction_id: int
    ) -> ReleaseMechanismContext | None: ...


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
