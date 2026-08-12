"""Construct reaction mechanism context and reusable mechanistic centers.

The direct reaction center describes net reactant-to-product changes.  A
mechanistic-center (MC) template retains that induced RC and adds non-net edges
touched by the ordered EPD.  Reaction-specific event order remains in the
mechanism-context payload rather than being conflated with the reusable MC.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import hashlib
import json
from typing import Any, Iterable, Sequence

import networkx as nx
from synkit.Graph.Feature.wl_hash import WLHash
from synkit.Graph.Mech import LWGEditor

from synepd.core.ingest import extract_graphs
from synepd.core.graph_codec import encode_graph

MECHANISM_CONTEXT_VERSION = "synepd.mechanistic-center.v1"
MECHANISTIC_CENTER_TEMPLATE_VERSION = "synepd.mechanistic-center-template.v1"

MC_NODE_ATTRIBUTES = (
    "element",
    "charge",
    "lone_pairs",
    "hcount",
    "radical",
    "mechanistic_roles",
)
MC_EDGE_ATTRIBUTES = (
    "order",
    "kekule_order",
    "standard_order",
    "mechanistic_roles",
)
MC_NODE_MATCH = nx.algorithms.isomorphism.categorical_node_match(
    MC_NODE_ATTRIBUTES,
    (None,) * len(MC_NODE_ATTRIBUTES),
)
MC_EDGE_MATCH = nx.algorithms.isomorphism.categorical_edge_match(
    MC_EDGE_ATTRIBUTES,
    (None,) * len(MC_EDGE_ATTRIBUTES),
)
MC_WL_HASHER = WLHash(
    node=list(MC_NODE_ATTRIBUTES),
    edge=list(MC_EDGE_ATTRIBUTES),
    iterations=3,
)


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


@dataclass(frozen=True)
class MechanisticCenterEdgeCounts:
    """Materialized comparison counts for one MC template versus its RC."""

    transition: int
    rc_extension: int
    transient_only: int


def mechanistic_centers_are_isomorphic(first: nx.Graph, second: nx.Graph) -> bool:
    """Compare MC templates using chemistry and mechanistic edge roles."""
    return nx.is_isomorphic(
        first,
        second,
        node_match=MC_NODE_MATCH,
        edge_match=MC_EDGE_MATCH,
    )


def mechanistic_center_wlhash(graph: nx.Graph) -> str:
    """Return a map-invariant prefilter hash for one MC template graph."""
    return MC_WL_HASHER.weisfeiler_lehman_graph_hash(graph)


def mechanistic_center_edge_counts(graph: nx.Graph) -> MechanisticCenterEdgeCounts:
    """Count non-net transition, RC-extension, and transient-only MC edges.

    ``transition`` captures EPD information absent from a plain net-change RC.
    ``rc_extension`` is the stricter topology metric: the transition edge lies
    outside the complete induced RC.  ``transient_only`` is absent from both
    endpoint structures.
    """
    transition = 0
    rc_extension = 0
    transient_only = 0
    for _, _, attributes in graph.edges(data=True):
        roles = set(attributes.get("mechanistic_roles", ()))
        if EdgeRole.TRANSITION.value not in roles:
            continue
        transition += 1
        if not roles.intersection({EdgeRole.CONTEXT.value, EdgeRole.NET_CHANGE.value}):
            rc_extension += 1
        if EdgeRole.TRANSIENT_ONLY.value in roles:
            transient_only += 1
    return MechanisticCenterEdgeCounts(
        transition=transition,
        rc_extension=rc_extension,
        transient_only=transient_only,
    )


def build_mechanistic_center_template(center: MechanisticCenter) -> nx.Graph:
    """Extract the reusable ``RC + transition-edge`` graph from one context."""
    return build_mechanistic_center_template_from_context(
        center.anchor_graph,
        formal_atom_maps=center.formal_atom_maps,
        formal_edges=center.formal_edges,
        edge_roles=center.edge_roles,
    )


def build_mechanistic_center_template_from_diagnostics(
    anchor_graph: nx.Graph,
    diagnostics: dict[str, Any],
) -> nx.Graph:
    """Reconstruct an MC template from a stored mechanism-context payload."""
    parsed_roles = {
        _parse_edge_key(key): frozenset(EdgeRole(role) for role in roles)
        for key, roles in diagnostics.get("edge_roles", {}).items()
    }
    return build_mechanistic_center_template_from_context(
        anchor_graph,
        formal_atom_maps=frozenset(
            int(atom_map) for atom_map in diagnostics.get("formal_atom_maps", ())
        ),
        formal_edges=frozenset(
            _edge_key(int(edge[0]), int(edge[1]))
            for edge in diagnostics.get("formal_edges", ())
        ),
        edge_roles=parsed_roles,
    )


def build_mechanistic_center_template_from_context(
    anchor_graph: nx.Graph,
    *,
    formal_atom_maps: frozenset[int],
    formal_edges: frozenset[tuple[int, int]],
    edge_roles: dict[tuple[int, int], frozenset[EdgeRole]],
) -> nx.Graph:
    """Build an accurate MC: induced RC plus EPD-only transition edges.

    The returned graph contains every formal RC atom, every edge induced among
    those atoms, and each non-net edge edited during EPD replay.  A transient
    edge absent from both endpoint structures is materialized with zero
    endpoint bond order and labelled ``transition``/``transient_only``.
    """
    anchor_by_map = _atom_map_to_node(anchor_graph)
    transition_edges = frozenset(
        edge for edge, roles in edge_roles.items() if EdgeRole.TRANSITION in roles
    )
    transient_edges = frozenset(
        edge for edge, roles in edge_roles.items() if EdgeRole.TRANSIENT_ONLY in roles
    )
    included_maps = set(formal_atom_maps)
    included_maps.update(atom_map for edge in transition_edges for atom_map in edge)
    missing_maps = included_maps - anchor_by_map.keys()
    if missing_maps:
        raise ValueError(
            "Mechanistic center references atom maps absent from its anchor: "
            f"{sorted(missing_maps)}"
        )

    template = nx.Graph()
    for atom_map in sorted(included_maps):
        attributes = dict(anchor_graph.nodes[anchor_by_map[atom_map]])
        attributes["atom_map"] = atom_map
        attributes["mechanistic_roles"] = [
            (
                NodeRole.NET_CENTER.value
                if atom_map in formal_atom_maps
                else NodeRole.EPD_CONTEXT.value
            )
        ]
        template.add_node(atom_map, **attributes)

    anchor_edges: dict[tuple[int, int], dict[str, Any]] = {}
    rc_induced_edges: set[tuple[int, int]] = set()
    for first, second, attributes in anchor_graph.edges(data=True):
        mapped_edge = _edge_key(
            _node_atom_map(anchor_graph, first),
            _node_atom_map(anchor_graph, second),
        )
        anchor_edges[mapped_edge] = dict(attributes)
        if mapped_edge[0] in formal_atom_maps and mapped_edge[1] in formal_atom_maps:
            rc_induced_edges.add(mapped_edge)

    for edge in sorted(rc_induced_edges | set(transition_edges)):
        attributes = dict(
            anchor_edges.get(
                edge,
                {
                    "order": (0.0, 0.0),
                    "kekule_order": (0.0, 0.0),
                    "sigma_order": (0.0, 0.0),
                    "pi_order": (0.0, 0.0),
                    "standard_order": 0.0,
                },
            )
        )
        roles: set[EdgeRole] = set()
        if edge in formal_edges:
            roles.add(EdgeRole.NET_CHANGE)
        elif edge in rc_induced_edges:
            roles.add(EdgeRole.CONTEXT)
        if edge in transition_edges:
            roles.add(EdgeRole.TRANSITION)
        if edge in transient_edges:
            roles.add(EdgeRole.TRANSIENT_ONLY)
        attributes["mechanistic_roles"] = sorted(role.value for role in roles)
        template.add_edge(*edge, **attributes)

    template.graph["mechanistic_center"] = {
        "construction_version": MECHANISTIC_CENTER_TEMPLATE_VERSION,
        "formal_atom_maps": sorted(formal_atom_maps),
        "formal_edges": [list(edge) for edge in sorted(formal_edges)],
        "rc_induced_edges": [list(edge) for edge in sorted(rc_induced_edges)],
        "transition_edges": [list(edge) for edge in sorted(transition_edges)],
        "transient_only_edges": [list(edge) for edge in sorted(transient_edges)],
    }
    return template


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


def _parse_edge_key(value: str) -> tuple[int, int]:
    first, separator, second = value.partition("-")
    if not separator:
        raise ValueError(f"Invalid serialized edge key: {value!r}")
    return _edge_key(int(first), int(second))
