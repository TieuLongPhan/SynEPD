"""Construct EPD-aware mechanistic centers from mapped reactions.

The direct reaction center describes net reactant-to-product changes.  A
mechanistic center adds atoms and edges touched temporarily by the ordered EPD
without changing the meaning of that direct center.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import hashlib
import json
from typing import Any, Iterable, Sequence

import networkx as nx
from synkit.Graph.Mech import LWGEditor

from synepd.core.ingest import extract_graphs
from synepd.core.graph_codec import encode_graph

MECHANISM_CONTEXT_VERSION = "synepd.mechanistic-center.v1"


class NodeRole(StrEnum):
    """A node's role in an EPD-aware mechanistic center."""

    NET_CENTER = "net_center"
    EPD_CONTEXT = "epd_context"
    NORMALIZATION_CONTEXT = "normalization_context"


class EdgeRole(StrEnum):
    """An edge's role in an EPD-aware mechanistic center."""

    NET_CHANGE = "net_change"
    TRANSITION = "transition"
    CONTEXT = "context"
    TRANSIENT_ONLY = "transient_only"


@dataclass(frozen=True)
class TransitionEvent:
    """One ordered sigma/pi edit reported while applying an EPD action."""

    action_index: int
    action: str
    atom_maps: tuple[int, int]
    field: str
    delta: float
    previous_value: float
    new_value: float
    removed: bool


@dataclass
class MechanisticCenter:
    """Direct RC, endpoint anchor, and ordered EPD transition information."""

    direct_center: nx.Graph
    anchor_graph: nx.Graph
    node_roles: dict[int, frozenset[NodeRole]]
    edge_roles: dict[tuple[int, int], frozenset[EdgeRole]]
    events: tuple[TransitionEvent, ...]
    epd_atom_maps: frozenset[int]
    formal_atom_maps: frozenset[int]
    formal_edges: frozenset[tuple[int, int]]


@dataclass(frozen=True)
class MechanismContextPayload:
    """Deterministic database payload for one mechanistic center."""

    anchor_graph: bytes
    events_json: str
    diagnostics_json: str
    context_hash: str


def serialize_mechanism_context(center: MechanisticCenter) -> MechanismContextPayload:
    """Serialize a center and derive its content-addressed identity."""
    anchor_blob = encode_graph(center.anchor_graph)
    events_json = json.dumps(
        [asdict(event) for event in center.events],
        sort_keys=True,
        separators=(",", ":"),
    )
    diagnostics = {
        "epd_atom_maps": sorted(center.epd_atom_maps),
        "formal_atom_maps": sorted(center.formal_atom_maps),
        "formal_edges": [list(edge) for edge in sorted(center.formal_edges)],
        "node_roles": {
            str(atom_map): sorted(role.value for role in roles)
            for atom_map, roles in sorted(center.node_roles.items())
        },
        "edge_roles": {
            f"{first}-{second}": sorted(role.value for role in roles)
            for (first, second), roles in sorted(center.edge_roles.items())
        },
    }
    diagnostics_json = json.dumps(diagnostics, sort_keys=True, separators=(",", ":"))
    context_hash = hashlib.sha256(
        anchor_blob + events_json.encode("utf-8") + diagnostics_json.encode("utf-8")
    ).hexdigest()
    return MechanismContextPayload(
        anchor_graph=anchor_blob,
        events_json=events_json,
        diagnostics_json=diagnostics_json,
        context_hash=context_hash,
    )


def build_mechanistic_center(
    rsmi: str,
    epd: Sequence[Sequence[Any]],
    *,
    editor: LWGEditor | None = None,
) -> MechanisticCenter:
    """Build an EPD-aware center for one atom-mapped reaction.

    ``rsmi`` must use the same atom-map namespace as ``epd``.  Product matching
    is intentionally left to the caller: the transition reports remain useful
    for explicitly documented surrogate representations as well as exact EPDs.
    """
    graphs = extract_graphs(rsmi)
    if graphs is None:
        raise ValueError("Could not extract ITS and reaction center from RSMI")

    its_graph, direct_center, _ = graphs
    edit_result = (editor or LWGEditor()).apply(rsmi, epd)
    return build_mechanistic_center_from_graphs(
        its_graph,
        direct_center,
        epd,
        step_reports=edit_result.step_reports,
    )


def build_mechanistic_center_from_graphs(
    its_graph: nx.Graph,
    direct_center: nx.Graph,
    epd: Sequence[Sequence[Any]],
    *,
    step_reports: Iterable[Any] = (),
) -> MechanisticCenter:
    """Build a mechanistic center from preconstructed endpoint graphs."""
    epd_maps = _epd_atom_maps(epd)
    its_by_map = _atom_map_to_node(its_graph)
    unknown = epd_maps - its_by_map.keys()
    if unknown:
        raise ValueError(f"EPD references atom maps absent from ITS: {sorted(unknown)}")

    rc_info = direct_center.graph.get("rc", {})
    formal_node_ids = set(rc_info.get("nodes", direct_center.nodes))
    formal_maps = frozenset(
        _node_atom_map(direct_center, node) for node in formal_node_ids
    )

    raw_formal_edges = rc_info.get("edges", ())
    formal_edges = frozenset(
        _edge_key(
            _node_atom_map(direct_center, first),
            _node_atom_map(direct_center, second),
        )
        for first, second in raw_formal_edges
    )

    anchor_maps = formal_maps | epd_maps
    anchor_nodes = [its_by_map[atom_map] for atom_map in sorted(anchor_maps)]
    anchor_graph = its_graph.subgraph(anchor_nodes).copy()

    events = tuple(_transition_events(step_reports))
    edited_edges = frozenset(event.atom_maps for event in events)

    node_roles: dict[int, frozenset[NodeRole]] = {}
    for atom_map in sorted(anchor_maps):
        roles: set[NodeRole] = set()
        if atom_map in formal_maps:
            roles.add(NodeRole.NET_CENTER)
        if atom_map in epd_maps and atom_map not in formal_maps:
            roles.add(NodeRole.EPD_CONTEXT)
        if atom_map in formal_maps and atom_map not in epd_maps:
            roles.add(NodeRole.NORMALIZATION_CONTEXT)
        node_roles[atom_map] = frozenset(roles)
        anchor_graph.nodes[its_by_map[atom_map]]["mechanistic_roles"] = sorted(
            role.value for role in roles
        )

    endpoint_edges: set[tuple[int, int]] = set()
    edge_roles: dict[tuple[int, int], frozenset[EdgeRole]] = {}
    for first, second in anchor_graph.edges:
        edge = _edge_key(
            _node_atom_map(anchor_graph, first),
            _node_atom_map(anchor_graph, second),
        )
        endpoint_edges.add(edge)
        roles: set[EdgeRole] = set()
        if edge in formal_edges:
            roles.add(EdgeRole.NET_CHANGE)
        if edge in edited_edges and edge not in formal_edges:
            roles.add(EdgeRole.TRANSITION)
        if edge not in formal_edges and edge not in edited_edges:
            roles.add(EdgeRole.CONTEXT)
        edge_roles[edge] = frozenset(roles)
        anchor_graph.edges[first, second]["mechanistic_roles"] = sorted(
            role.value for role in roles
        )

    for edge in sorted(edited_edges - endpoint_edges):
        roles = {EdgeRole.TRANSITION, EdgeRole.TRANSIENT_ONLY}
        if edge in formal_edges:
            roles.add(EdgeRole.NET_CHANGE)
        edge_roles[edge] = frozenset(roles)

    anchor_graph.graph["mechanistic_center"] = {
        "epd_atom_maps": sorted(epd_maps),
        "formal_atom_maps": sorted(formal_maps),
        "formal_edges": [list(edge) for edge in sorted(formal_edges)],
        "event_count": len(events),
    }
    return MechanisticCenter(
        direct_center=direct_center,
        anchor_graph=anchor_graph,
        node_roles=node_roles,
        edge_roles=edge_roles,
        events=events,
        epd_atom_maps=epd_maps,
        formal_atom_maps=formal_maps,
        formal_edges=formal_edges,
    )


def _transition_events(step_reports: Iterable[Any]) -> Iterable[TransitionEvent]:
    for report in step_reports:
        for change in report.edge_changes:
            yield TransitionEvent(
                action_index=int(report.action_index),
                action=str(report.action),
                atom_maps=_edge_key(*change.atom_maps),
                field=str(change.field),
                delta=float(change.delta),
                previous_value=float(change.previous_value),
                new_value=float(change.new_value),
                removed=bool(change.removed),
            )


def _epd_atom_maps(epd: Sequence[Sequence[Any]]) -> frozenset[int]:
    maps: set[int] = set()
    for index, step in enumerate(epd, start=1):
        if len(step) != 3:
            raise ValueError(f"EPD arrow {index} must be [type, source, target]")
        for endpoint in step[1:]:
            maps.update(int(atom_map) for atom_map in endpoint)
    return frozenset(maps)


def _atom_map_to_node(graph: nx.Graph) -> dict[int, Any]:
    return {_node_atom_map(graph, node): node for node in graph.nodes}


def _node_atom_map(graph: nx.Graph, node: Any) -> int:
    return int(graph.nodes[node].get("atom_map", node))


def _edge_key(first: int, second: int) -> tuple[int, int]:
    return (first, second) if first <= second else (second, first)
