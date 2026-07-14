#!/usr/bin/env python3
"""Verify that SynEPD EPDs transform reactants into their mapped products.

Examples
--------
Verify all polar records and show only problems::

    python epd_verification.py

Inspect selected records and write a machine-readable report::

    python epd_verification.py --id 155 --id 1499 --output epd_report.json
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
from typing import Any, Iterable

from synkit.Graph.Mech import LWGEditor
from synkit.Graph.Mech.electron_accounting import atom_map_to_node
from synkit.Graph.Mech.lwg_ops import normalize_lwg_graph

DEFAULT_DATA_PATH = Path("data/polar.json")


def _verification_rsmi(record: dict[str, Any]) -> str:
    """Return the RSMI used for electron-pair verification.

    The canonical ``rsmi`` remains the chemically intended representation.
    Records with an open-shell endpoint may provide one explicitly documented
    closed-shell product fragment for the pair-only LWG editor.
    """
    rsmi = record["rsmi"]
    representation = record.get("epd_representation")
    if not representation or representation.get("mode") == "exact":
        return rsmi

    chemical_fragment = representation.get("chemical_product_fragment")
    lwg_fragment = representation.get("lwg_product_fragment")
    if not chemical_fragment or not lwg_fragment:
        raise ValueError(
            "Non-exact EPD representations require chemical_product_fragment "
            "and lwg_product_fragment."
        )

    reactants, products = rsmi.split(">>", 1)
    if products.count(chemical_fragment) != 1:
        raise ValueError(
            "chemical_product_fragment must occur exactly once on the product side."
        )
    return f"{reactants}>>{products.replace(chemical_fragment, lwg_fragment, 1)}"


def _step_reports(result: Any) -> list[dict[str, Any]]:
    """Return JSON-friendly SynKit action diagnostics."""
    return [asdict(report) for report in result.step_reports]


def _failure_context(
    rsmi: str, epd: list[list[Any]], editor: LWGEditor
) -> dict[str, Any] | None:
    """Locate the first failing action and record its editable graph state."""
    reactants = rsmi.split(">>", 1)[0]
    graph = editor._smiles_to_lwg(reactants)

    for index, step in enumerate(epd):
        try:
            editor._apply_action(
                graph, action=str(step[0]), source=list(step[1]), target=list(step[2])
            )
            normalize_lwg_graph(graph, in_place=True)
        except Exception as exc:
            maps = sorted(set(int(atom) for atoms in step[1:] for atom in atoms))
            lookup = atom_map_to_node(graph)
            atoms = {
                atom_map: {
                    key: graph.nodes[lookup[atom_map]].get(key)
                    for key in ("element", "charge", "lone_pairs", "hcount")
                }
                for atom_map in maps
                if atom_map in lookup
            }
            edges = {}
            for atom_maps in (step[1], step[2]):
                if len(atom_maps) != 2:
                    continue
                first, second = (int(atom) for atom in atom_maps)
                if (
                    first in lookup
                    and second in lookup
                    and graph.has_edge(lookup[first], lookup[second])
                ):
                    edges[f"{first}-{second}"] = {
                        key: graph.edges[lookup[first], lookup[second]].get(key)
                        for key in (
                            "order",
                            "kekule_order",
                            "sigma_order",
                            "pi_order",
                            "aromatic",
                            "bond_type",
                        )
                    }
            return {
                "failed_action_index": index,
                "failed_action": step,
                "failure": f"{type(exc).__name__}: {exc}",
                "atom_state_before_failure": atoms,
                "edge_state_before_failure": edges,
            }
    return None


def verify_record(record: dict[str, Any], editor: LWGEditor) -> dict[str, Any]:
    """Apply one record's EPD and compare the final graph to its product."""
    representation = record.get("epd_representation")
    result_base = {
        "id": record["id"],
        "reaction_name": record["reaction_name"],
        "tax_codes": record.get("tax_codes") or [record["tax_code"]],
        "rsmi": record["rsmi"],
        "epd": record.get("epd", []),
    }
    if representation:
        result_base["epd_representation"] = representation

    try:
        verification_rsmi = _verification_rsmi(record)
        result = editor.apply(verification_rsmi, record.get("epd", []))
    except Exception as exc:
        return {
            **result_base,
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "failure_context": _failure_context(
                locals().get("verification_rsmi", record["rsmi"]),
                record.get("epd", []),
                editor,
            ),
        }

    is_surrogate = bool(
        representation and representation.get("mode") not in (None, "exact")
    )
    status = (
        "surrogate_pass"
        if result.matches_product and is_surrogate
        else "pass" if result.matches_product else "mismatch"
    )
    verified = {
        **result_base,
        "status": status,
        "matches_product": result.matches_product,
        "structural_match": result.structural_match,
        "charge_match": result.charge_match,
        "smiles_match": result.smiles_match,
        "final_smiles": result.final_smiles,
        "product_smiles": result.product_smiles,
        "step_reports": _step_reports(result),
    }
    if verification_rsmi != record["rsmi"]:
        verified["verification_rsmi"] = verification_rsmi
        chemical_products = record["rsmi"].split(">>", 1)[1]
        chemical_graph = editor._smiles_to_lwg(chemical_products)
        verified["chemical_product_smiles"] = editor.graph_to_smiles(chemical_graph)
    return verified


def _load_records(path: Path, ids: set[int] | None) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        records = json.load(handle)["records"]
    if ids is None:
        return records
    found = {record["id"] for record in records if record["id"] in ids}
    missing = sorted(ids - found)
    if missing:
        raise ValueError(f"Record IDs not found: {missing}")
    return [record for record in records if record["id"] in ids]


def verify_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Verify all supplied records with a shared SynKit editor instance."""
    editor = LWGEditor()
    return [verify_record(record, editor) for record in records]


def _print_summary(results: list[dict[str, Any]], include_passes: bool) -> None:
    counts = {
        status: sum(result["status"] == status for result in results)
        for status in ("pass", "surrogate_pass", "mismatch", "error")
    }
    print(
        "EPD verification: "
        f"{counts['pass']} pass, {counts['surrogate_pass']} surrogate pass, "
        f"{counts['mismatch']} mismatch, "
        f"{counts['error']} error"
    )
    for result in results:
        if result["status"] == "pass" and not include_passes:
            continue
        print(
            f"[{result['status'].upper()}] ID {result['id']}: "
            f"{result['reaction_name']}"
        )
        if result["status"] == "error":
            print(f"  {result['error']}")
            context = result.get("failure_context")
            if context:
                print(
                    f"  failing arrow {context['failed_action_index']}: "
                    f"{context['failed_action']}"
                )
        elif result["status"] == "mismatch":
            print(
                "  matches: "
                f"structure={result['structural_match']} "
                f"charge={result['charge_match']} "
                f"smiles={result['smiles_match']}"
            )
            print(f"  transformed: {result['final_smiles']}")
            print(f"  expected:    {result['product_smiles']}")
        elif result["status"] == "surrogate_pass":
            representation = result["epd_representation"]
            print(
                "  exact two-electron edit match against the documented "
                f"{representation['mode']}"
            )
            print(f"  chemical:    {result.get('chemical_product_smiles')}")
            print(f"  surrogate:   {result['product_smiles']}")
            print(f"  limitation:  {representation.get('limitation')}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data",
        type=Path,
        default=DEFAULT_DATA_PATH,
        help=f"SynEPD JSON data file (default: {DEFAULT_DATA_PATH})",
    )
    parser.add_argument(
        "--id",
        type=int,
        action="append",
        dest="ids",
        help="Record ID to verify; repeat to select multiple records",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write the full JSON report to this path",
    )
    parser.add_argument(
        "--include-passes",
        action="store_true",
        help="Also print passing records",
    )
    parser.add_argument(
        "--issues-only",
        action="store_true",
        help="When writing --output, include only mismatches and errors",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with status 1 if any record mismatches or errors",
    )
    args = parser.parse_args()

    try:
        records = _load_records(args.data, set(args.ids) if args.ids else None)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    results = verify_records(records)
    _print_summary(results, args.include_passes)

    if args.output:
        report_results = (
            [result for result in results if result["status"] != "pass"]
            if args.issues_only
            else results
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {
                    "data": str(args.data),
                    "verified_record_count": len(results),
                    "issue_count": sum(
                        result["status"] != "pass" for result in results
                    ),
                    "results": report_results,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"Wrote report: {args.output}")

    has_issues = any(result["status"] != "pass" for result in results)
    return 1 if args.strict and has_issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
