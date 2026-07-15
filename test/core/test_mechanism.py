import json
from pathlib import Path

from synepd.core.mechanism import (
    EdgeRole,
    NodeRole,
    build_mechanistic_center,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _record(record_id):
    payload = json.loads(
        (REPOSITORY_ROOT / "data" / "polar.json").read_text(encoding="utf-8")
    )
    return next(record for record in payload["records"] if record["id"] == record_id)


def test_mechanistic_center_includes_context_atom_and_transition_edge():
    record = _record(106)

    center = build_mechanistic_center(record["rsmi"], record["epd"])

    assert center.formal_atom_maps == frozenset({1, 4, 7})
    assert center.epd_atom_maps == frozenset({1, 4, 5, 7})
    assert center.node_roles[5] == frozenset({NodeRole.EPD_CONTEXT})
    assert EdgeRole.TRANSITION in center.edge_roles[(5, 7)]
    assert EdgeRole.NET_CHANGE not in center.edge_roles[(5, 7)]
    assert len(center.events) == 4


def test_mechanistic_center_marks_formal_nodes_not_touched_by_epd_as_context():
    record = _record(1599)

    center = build_mechanistic_center(record["rsmi"], record["epd"])

    assert NodeRole.NORMALIZATION_CONTEXT in center.node_roles[2]
    assert NodeRole.NET_CENTER in center.node_roles[2]
