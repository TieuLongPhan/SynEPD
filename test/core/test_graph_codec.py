import pickle
import zlib

import networkx as nx
import pytest

from synepd.core.graph_codec import (
    GRAPH_FORMAT,
    LEGACY_PICKLE_FORMAT,
    decode_graph,
    encode_graph,
)


def test_graph_codec_preserves_paired_attributes_and_tuple_keys():
    graph = nx.Graph()
    graph.add_node(1, element=("C", "C"), charge=(0, 1), atom_map=1)
    graph.add_node(2, element=("O", "O"), charge=(0, -1), atom_map=2)
    graph.add_edge(1, 2, order=(1.0, 2.0), standard_order=-1.0)
    graph.graph["rc"] = {"edge_reasons": {(1, 2): ["standard_order"]}}

    decoded = decode_graph(encode_graph(graph), GRAPH_FORMAT)

    assert decoded.nodes[1]["element"] == ("C", "C")
    assert decoded.edges[1, 2]["order"] == (1.0, 2.0)
    assert decoded.graph["rc"]["edge_reasons"] == {(1, 2): ["standard_order"]}


def test_legacy_graph_loader_allows_networkx_graphs():
    graph = nx.Graph()
    graph.add_edge(1, 2, order=(1.0, 0.0))
    # SynEPD v0.1.0 pickles can contain NetworkX cached view objects.
    graph.nodes
    graph.edges
    graph.degree
    graph.adj
    blob = zlib.compress(pickle.dumps(graph))

    decoded = decode_graph(blob, LEGACY_PICKLE_FORMAT, allow_legacy=True)

    assert decoded.edges[1, 2]["order"] == (1.0, 0.0)


def test_legacy_graph_loader_requires_explicit_migration_opt_in():
    blob = zlib.compress(pickle.dumps(nx.Graph()))

    with pytest.raises(ValueError, match="disabled in normal runtime"):
        decode_graph(blob, LEGACY_PICKLE_FORMAT)


def test_legacy_graph_loader_rejects_arbitrary_globals():
    class UnsafePayload:
        def __reduce__(self):
            return eval, ("40 + 2",)

    blob = zlib.compress(pickle.dumps(UnsafePayload()))

    with pytest.raises(pickle.UnpicklingError, match="forbidden global"):
        decode_graph(blob, LEGACY_PICKLE_FORMAT, allow_legacy=True)
