#!/usr/bin/env python3
"""Strictly replay SynEPD electron flow and verify every mapped product.

Examples
--------
Verify all polar records and show only problems::

    python epd_verification.py

Inspect selected records and write a machine-readable report::

    python epd_verification.py --id 155 --id 1499 --output epd_report.json
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, replace
import json
from pathlib import Path
from typing import Any, Iterable

from synkit.Graph.Mech import LWGEditor
from synkit.Graph.Mech.electron_accounting import atom_map_to_node
from synkit.Graph.Mech.lwg_ops import normalize_lwg_graph
from synkit.Mechanism import (
    MechanismRecord,
    MechanismReplayer,
    mechanism_from_legacy_epd,
)

from synepd.core.representation import representation_verification_rsmi

DEFAULT_DATA_PATH = Path("data/polar.json")
ISSUE_STATUSES = frozenset({"mismatch", "error"})


def _verification_rsmi(record: dict[str, Any]) -> str:
    """Return the exact or formal-charge-surrogate verification endpoint."""
    return representation_verification_rsmi(
        record["rsmi"], record.get("epd_representation")
    )


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

    # Only modes that rewrite the verification endpoint are surrogates.  The
    # closed-shell-pair policy keeps the chemical endpoint unchanged and is
    # classified by the strict verifier as a normalized exact replay.
    is_surrogate = verification_rsmi != record["rsmi"]
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
    """Run the compatibility endpoint check with a shared legacy editor."""
    editor = LWGEditor()
    return [verify_record(record, editor) for record in records]


def _strict_mechanism_record(record: dict[str, Any]) -> MechanismRecord:
    """Adapt a release record to SynKit's resource-aware mechanism model."""
    representation = record.get("epd_representation") or {}
    verification_rsmi = _verification_rsmi(record)
    mechanism = mechanism_from_legacy_epd(
        verification_rsmi,
        record.get("epd", []),
        provenance={
            "format": "synepd.clean.polar.v2",
            "source_id": record["id"],
        },
    )
    if representation.get("mode") == "closed_shell_pair":
        mechanism = replace(
            mechanism,
            metadata={
                "endpoint_resource_policy": "closed_shell_pair",
                "closed_shell_atom_maps": representation["closed_shell_atom_maps"],
            },
        )
    return mechanism


def verify_record_strict(
    record: dict[str, Any], replayer: MechanismReplayer
) -> dict[str, Any]:
    """Verify ordered electron resources, charge deltas, and the endpoint."""
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
        mechanism = _strict_mechanism_record(record)
        replay = replayer.replay(mechanism)
    except Exception as exc:
        return {
            **result_base,
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
        }

    certificate = replay.certificate
    issue_payloads = [issue.to_dict() for issue in certificate.issues]
    verified = {
        **result_base,
        "status": "pass" if certificate.status == "VALID" else "mismatch",
        "verification_level": certificate.verification_level,
        "matches_product": certificate.endpoint_match,
        "transition_valid": certificate.transition_valid,
        "delta_charge_match": certificate.delta_charge_match,
        "endpoint_match": certificate.endpoint_match,
        "absolute_lwg_valid": certificate.absolute_lwg_valid,
        "absolute_residuals_invariant": certificate.absolute_residuals_invariant,
        "endpoint_resource_policy": certificate.endpoint_resource_policy,
        "normalization_evidence": [
            dict(item) for item in certificate.normalization_evidence
        ],
        "diagnostics": list(certificate.diagnostics),
        "issues": issue_payloads,
        "issue_codes": [issue["code"] for issue in issue_payloads],
        "step_reports": [dict(report) for report in certificate.step_reports],
        "raw_endpoint_match": certificate.final_match.get(
            "raw_matches", certificate.endpoint_match
        ),
    }
    verification_rsmi = _verification_rsmi(record)
    if verification_rsmi != record["rsmi"]:
        verified["verification_rsmi"] = verification_rsmi
    return verified


def verify_records_strict(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run strict, resource-aware replay for every supplied release record."""
    replayer = MechanismReplayer(validation="strict")
    return [verify_record_strict(record, replayer) for record in records]


def _is_issue(result: dict[str, Any]) -> bool:
    """Return whether a verification result is a mismatch or execution error."""
    return result.get("status") in ISSUE_STATUSES


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
    levels = Counter(
        result["verification_level"]
        for result in results
        if result.get("status") == "pass" and result.get("verification_level")
    )
    if levels:
        order = ("EXACT", "NORMALIZED", "DELTA_CONSISTENT")
        summary = ", ".join(
            f"{levels[level]} {level}" for level in order if levels.get(level)
        )
        print(f"Verification levels: {summary}")
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
            if "issues" in result:
                print("  strict issues: " + ", ".join(result.get("issue_codes", ())))
            else:
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
    parser.add_argument(
        "--legacy-editor",
        action="store_true",
        help="Use the endpoint-only compatibility editor instead of strict replay",
    )
    args = parser.parse_args()

    try:
        records = _load_records(args.data, set(args.ids) if args.ids else None)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    results = (
        verify_records(records)
        if args.legacy_editor
        else verify_records_strict(records)
    )
    _print_summary(results, args.include_passes)

    if args.output:
        report_results = (
            [result for result in results if _is_issue(result)]
            if args.issues_only
            else results
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {
                    "data": str(args.data),
                    "verified_record_count": len(results),
                    "issue_count": sum(_is_issue(result) for result in results),
                    "results": report_results,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"Wrote report: {args.output}")

    has_issues = any(_is_issue(result) for result in results)
    return 1 if args.strict and has_issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
