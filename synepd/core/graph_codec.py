"""Versioned, safe serialization for NetworkX reaction graphs."""

from __future__ import annotations

import base64
import io
import json
import pickle
import zlib
from typing import Any

import networkx as nx

GRAPH_FORMAT = "synepd.node-link-json.zlib.v1"
LEGACY_PICKLE_FORMAT = "pickle.gz"


def encode_graph(graph: nx.Graph) -> bytes:
    """Encode a NetworkX graph as compressed, type-preserving JSON."""
    payload = {
        "codec": GRAPH_FORMAT,
        "directed": graph.is_directed(),
        "multigraph": graph.is_multigraph(),
        "graph": _encode_value(dict(graph.graph)),
        "nodes": [
            {"id": _encode_value(node), "attrs": _encode_value(dict(attrs))}
            for node, attrs in graph.nodes(data=True)
        ],
        "edges": _encoded_edges(graph),
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return zlib.compress(serialized, level=9)


def decode_graph(
    raw: Any, graph_format: str, *, allow_legacy: bool = False
) -> nx.Graph:
    """Decode a graph using its declared format.

    Legacy pickles require an explicit migration-only opt-in and are read by a
    restricted unpickler. Normal query, manager, and web paths reject them.
    """
    compressed = _as_bytes(raw)
    if graph_format == GRAPH_FORMAT:
        return _decode_json_graph(zlib.decompress(compressed))
    if graph_format == LEGACY_PICKLE_FORMAT:
        if not allow_legacy:
            raise ValueError(
                "Legacy pickle graphs are disabled in normal runtime; migrate the database"
            )
        return _restricted_legacy_load(zlib.decompress(compressed))
    raise ValueError(f"Unsupported graph format: {graph_format!r}")


def _encoded_edges(graph: nx.Graph) -> list[dict[str, Any]]:
    if graph.is_multigraph():
        return [
            {
                "source": _encode_value(first),
                "target": _encode_value(second),
                "key": _encode_value(key),
                "attrs": _encode_value(dict(attrs)),
            }
            for first, second, key, attrs in graph.edges(keys=True, data=True)
        ]
    return [
        {
            "source": _encode_value(first),
            "target": _encode_value(second),
            "attrs": _encode_value(dict(attrs)),
        }
        for first, second, attrs in graph.edges(data=True)
    ]


def _decode_json_graph(serialized: bytes) -> nx.Graph:
    payload = json.loads(serialized.decode("utf-8"))
    if payload.get("codec") != GRAPH_FORMAT:
        raise ValueError("Graph payload codec does not match its declared format")

    directed = bool(payload.get("directed"))
    multigraph = bool(payload.get("multigraph"))
    if multigraph and directed:
        graph: nx.Graph = nx.MultiDiGraph()
    elif multigraph:
        graph = nx.MultiGraph()
    elif directed:
        graph = nx.DiGraph()
    else:
        graph = nx.Graph()

    graph.graph.update(_decode_value(payload["graph"]))
    for node in payload["nodes"]:
        graph.add_node(
            _decode_value(node["id"]),
            **_decode_value(node["attrs"]),
        )
    for edge in payload["edges"]:
        first = _decode_value(edge["source"])
        second = _decode_value(edge["target"])
        attrs = _decode_value(edge["attrs"])
        if multigraph:
            graph.add_edge(first, second, _decode_value(edge["key"]), **attrs)
        else:
            graph.add_edge(first, second, **attrs)
    return graph


def _encode_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, tuple):
        return {"__synepd_type__": "tuple", "items": [_encode_value(v) for v in value]}
    if isinstance(value, list):
        return [_encode_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return {
            "__synepd_type__": "set",
            "items": [_encode_value(item) for item in sorted(value, key=repr)],
        }
    if isinstance(value, bytes):
        return {
            "__synepd_type__": "bytes",
            "data": base64.b64encode(value).decode("ascii"),
        }
    if isinstance(value, dict):
        return {
            "__synepd_type__": "dict",
            "items": [
                [_encode_value(key), _encode_value(item)]
                for key, item in sorted(value.items(), key=lambda pair: repr(pair[0]))
            ],
        }
    if hasattr(value, "item"):
        return _encode_value(value.item())
    raise TypeError(f"Graph attribute type is not JSON serializable: {type(value)!r}")


def _decode_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode_value(item) for item in value]
    if not isinstance(value, dict) or "__synepd_type__" not in value:
        return value
    value_type = value["__synepd_type__"]
    if value_type == "tuple":
        return tuple(_decode_value(item) for item in value["items"])
    if value_type == "set":
        return set(_decode_value(item) for item in value["items"])
    if value_type == "bytes":
        return base64.b64decode(value["data"])
    if value_type == "dict":
        return {_decode_value(key): _decode_value(item) for key, item in value["items"]}
    raise ValueError(f"Unsupported encoded graph value type: {value_type!r}")


class _RestrictedGraphUnpickler(pickle.Unpickler):
    _ALLOWED = {
        ("networkx.classes.graph", "Graph"),
        ("networkx.classes.digraph", "DiGraph"),
        ("networkx.classes.multigraph", "MultiGraph"),
        ("networkx.classes.multidigraph", "MultiDiGraph"),
        ("networkx.classes.reportviews", "NodeView"),
        ("networkx.classes.reportviews", "EdgeView"),
        ("networkx.classes.reportviews", "DegreeView"),
        ("networkx.classes.reportviews", "OutEdgeView"),
        ("networkx.classes.reportviews", "InEdgeView"),
        ("networkx.classes.reportviews", "MultiEdgeView"),
        ("networkx.classes.reportviews", "OutMultiEdgeView"),
        ("networkx.classes.reportviews", "InMultiEdgeView"),
        ("networkx.classes.coreviews", "AdjacencyView"),
    }

    def find_class(self, module: str, name: str) -> Any:
        if (module, name) not in self._ALLOWED:
            raise pickle.UnpicklingError(
                f"Legacy graph pickle requested forbidden global {module}.{name}"
            )
        return super().find_class(module, name)


def _restricted_legacy_load(serialized: bytes) -> nx.Graph:
    graph = _RestrictedGraphUnpickler(io.BytesIO(serialized)).load()
    if not isinstance(graph, nx.Graph):
        raise pickle.UnpicklingError("Legacy graph payload did not contain a graph")
    return graph


def _as_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, memoryview):
        return value.tobytes()
    return bytes(value)
