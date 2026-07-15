"""Validate EPD atom-map references against an RSMI reaction center."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from synepd.core.ingest import extract_graphs


@dataclass(frozen=True)
class EPDReactionCenterCheck:
    """Comparison of atom maps referenced by EPD arrows and the RSMI center."""

    epd_atom_maps: frozenset[int]
    reaction_center_atom_maps: frozenset[int]
    maps_not_in_reaction_center: frozenset[int]
    reaction_center_maps_not_in_epd: frozenset[int]
    errors: tuple[str, ...] = ()

    @property
    def matches(self) -> bool:
        """Return whether the EPD references exactly the reaction-center maps."""
        return not (
            self.errors
            or self.maps_not_in_reaction_center
            or self.reaction_center_maps_not_in_epd
        )

    @property
    def covers_reaction_center(self) -> bool:
        """Return whether every reaction-center map is referenced by an EPD arrow.

        EPD arrows may legitimately reference context atoms outside the formal
        reaction center, such as a carbonyl oxygen in esterification.
        """
        return not self.errors and not self.reaction_center_maps_not_in_epd


def check_epd_reaction_center(
    rsmi: str, epd: Iterable[object]
) -> EPDReactionCenterCheck:
    """Check that EPD arrows cover all atom maps of the RSMI center.

    The EPD format stores RSMI atom-map numbers, not RDKit's zero-based atom
    indices. The reaction center is extracted from the mapped reaction using
    the same ``extract_graphs`` helper used by database construction. Extra
    EPD atom maps are reported as context, but do not invalidate coverage.
    """
    try:
        epd_maps = _epd_atom_maps(epd)
    except ValueError as exc:
        return EPDReactionCenterCheck(
            epd_atom_maps=frozenset(),
            reaction_center_atom_maps=frozenset(),
            maps_not_in_reaction_center=frozenset(),
            reaction_center_maps_not_in_epd=frozenset(),
            errors=(str(exc),),
        )

    graphs = extract_graphs(rsmi)
    if graphs is None:
        return EPDReactionCenterCheck(
            epd_atom_maps=epd_maps,
            reaction_center_atom_maps=frozenset(),
            maps_not_in_reaction_center=epd_maps,
            reaction_center_maps_not_in_epd=frozenset(),
            errors=("Could not extract a reaction center from RSMI",),
        )

    _, reaction_center, _ = graphs
    center_maps = frozenset(
        int(attributes.get("atom_map", node))
        for node, attributes in reaction_center.nodes(data=True)
    )
    return EPDReactionCenterCheck(
        epd_atom_maps=epd_maps,
        reaction_center_atom_maps=center_maps,
        maps_not_in_reaction_center=epd_maps - center_maps,
        reaction_center_maps_not_in_epd=center_maps - epd_maps,
    )


def _epd_atom_maps(epd: Iterable[object]) -> frozenset[int]:
    maps: set[int] = set()
    for arrow_index, arrow in enumerate(epd, start=1):
        if not isinstance(arrow, (list, tuple)) or len(arrow) != 3:
            raise ValueError(f"EPD arrow {arrow_index} must be [type, source, target]")
        _, source, target = arrow
        for endpoint_name, endpoint in (("source", source), ("target", target)):
            if not isinstance(endpoint, (list, tuple)):
                raise ValueError(
                    f"EPD arrow {arrow_index} {endpoint_name} must be a list of atom maps"
                )
            for atom_map in endpoint:
                if (
                    not isinstance(atom_map, int)
                    or isinstance(atom_map, bool)
                    or atom_map <= 0
                ):
                    raise ValueError(
                        f"EPD arrow {arrow_index} has invalid atom map {atom_map!r}"
                    )
                maps.add(atom_map)
    return frozenset(maps)
