"""Run representative SynEPD 0.4.0 Python query scenarios."""

from __future__ import annotations

import argparse
from pathlib import Path

from synepd.query import SynEPDQuery


def run_queries(db_path: Path) -> None:
    """Print one result from each principal v0.4 query path."""
    with SynEPDQuery(db_path) as query:
        reaction = query.reaction("polar_001321")
        if reaction is None:
            raise RuntimeError("Expected v0.4 reference polar_001321 is missing")
        print("reaction:", reaction["name"])
        print(
            "Mitsunobu records:",
            [row["case_id"] for row in query.search_reactions("Mitsunobu")],
        )

        ethanol = query.reactions_for_molecule("CCO", role="reactant")
        print("ethanol reactant matches:", len(ethanol))

        path = query.taxonomy_path("POLAR.04.01.012")
        print("taxonomy:", " > ".join(node["name"] for node in path))

        sequence = query.reactions_by_arrow_sequence(["LP-/Sigma+", "Sigma-/LP+"])
        print("LP→σ then σ→LP mechanisms:", len(sequence))

        mechanism = query.mechanism("polar_001321")
        print(
            "ITS / RC / MC edges:",
            mechanism["its"]["graph_data"].number_of_edges(),
            mechanism["rc"]["template_graph"].number_of_edges(),
            mechanism["mc"]["template_graph"].number_of_edges(),
        )

        direct = query.epd("CC(=O)Cl.CCO>>CC(=O)OCC")
        print(
            "EPD query path / arrows:",
            direct.get("path"),
            len(direct.get("arrows", [])),
        )

        neighbors = query.template_neighbors(case_id="polar_001321")
        print("shared RC template:", len(neighbors))

        xrefs = query.reaction_xrefs("polar_001321")
        print("direct or inherited RXNO/MOP links:", len(xrefs))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "database",
        nargs="?",
        type=Path,
        default=Path("data/epdb.sqlite"),
        help="path to a SynEPD 0.4.0 SQLite release",
    )
    args = parser.parse_args()
    run_queries(args.database)


if __name__ == "__main__":
    main()
