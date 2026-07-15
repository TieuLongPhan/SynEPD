"""Atom-map aware helpers for documented non-exact EPD representations."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

import networkx as nx
from rdkit import Chem
from synkit.IO import rsmi_to_graph
from synkit.Graph.Matcher.subgraph_matcher import SubgraphSearchEngine

from synepd.core.ingest import extract_graphs

ATOM_MAP_DICTIONARY_FIELDS = frozenset(
    {
        "chemical_oxidation_states",
        "lwg_formal_charge_overrides",
        "symmetry_equivalent_charge_overrides",
    }
)
ATOM_MAP_SCALAR_FIELDS = frozenset({"source_atom", "target_atom"})
ATOM_MAPPED_SMILES_FIELDS = frozenset(
    {"chemical_product_fragment", "lwg_product_fragment"}
)


def find_atom_map_translation(source_rsmi: str, target_its: nx.Graph) -> dict[int, int]:
    """Map source RSMI atom maps into a target ITS graph namespace."""
    translations = find_atom_map_translations(source_rsmi, target_its, max_results=1)
    return translations[0] if translations else {}


def find_atom_map_translations(
    source_rsmi: str,
    target_its: nx.Graph,
    *,
    max_results: int = 128,
) -> list[dict[int, int]]:
    """Return bounded source-to-target atom-map translations."""
    source_res = extract_graphs(source_rsmi)
    if source_res is None:
        return []
    source_its, _, _ = source_res
    return find_graph_atom_map_translations(
        source_its, target_its, max_results=max_results
    )


def find_graph_atom_map_translations(
    source_its: nx.Graph,
    target_its: nx.Graph,
    *,
    max_results: int = 128,
) -> list[dict[int, int]]:
    """Return bounded map translations between isomorphic ITS graphs."""
    mappings = SubgraphSearchEngine().find_subgraph_mappings(
        host=target_its,
        pattern=source_its,
        node_attrs=["element", "charge"],
        edge_attrs=["order"],
        max_results=max_results,
        threshold=max_results * 4,
        strict_cc_count=False,
    )
    translations = []
    seen = set()
    for mapping in mappings:
        translation = {
            int(source_its.nodes[source].get("atom_map", source)): int(
                target_its.nodes[target].get("atom_map", target)
            )
            for source, target in mapping.items()
        }
        key = tuple(sorted(translation.items()))
        if key not in seen:
            seen.add(key)
            translations.append(translation)
    return translations


def find_reactant_atom_map_translations(
    source_rsmi: str,
    target_rsmi: str,
    *,
    max_results: int = 128,
) -> list[dict[int, int]]:
    """Map reactant atom maps, retaining symmetry for EPD verification.

    Full ITS matching can over-constrain aromatic or resonance-equivalent
    reactant embeddings by their product-side Kekulé assignment. Mechanistic
    arrows originate in the reactant graph, so alternative reactant embeddings
    must remain available until endpoint verification selects one.
    """
    source_reactant, _ = rsmi_to_graph(source_rsmi, drop_non_aam=True)
    target_reactant, _ = rsmi_to_graph(target_rsmi, drop_non_aam=True)
    if source_reactant is None or target_reactant is None:
        return []
    return find_graph_atom_map_translations(
        source_reactant,
        target_reactant,
        max_results=max_results,
    )


def remap_epd(
    epd: Sequence[Sequence[Any]], atom_map: Mapping[int, int]
) -> list[list[Any]]:
    """Return an EPD in ``atom_map``'s target namespace."""
    return [
        [
            str(action),
            [int(atom_map.get(int(value), int(value))) for value in source],
            [int(atom_map.get(int(value), int(value))) for value in target],
        ]
        for action, source, target in epd
    ]


def remap_representation(
    representation: Mapping[str, Any] | None,
    atom_map: Mapping[int, int],
    *,
    namespace: str | None = None,
) -> dict[str, Any] | None:
    """Remap every documented atom-map-bearing representation field.

    Unknown fields are retained unchanged so representation metadata remains
    forward compatible. Known mapped SMILES fragments are parsed with RDKit,
    avoiding unsafe textual substitutions of map numbers.
    """
    if representation is None:
        return None

    result = deepcopy(dict(representation))
    for field in ATOM_MAP_DICTIONARY_FIELDS:
        value = result.get(field)
        if not isinstance(value, Mapping):
            continue
        result[field] = {
            str(atom_map.get(int(key), int(key))): item for key, item in value.items()
        }

    electron_step = result.get("unrepresented_electron_step")
    if isinstance(electron_step, Mapping):
        electron_step = dict(electron_step)
        for field in ATOM_MAP_SCALAR_FIELDS:
            if field in electron_step:
                value = int(electron_step[field])
                electron_step[field] = int(atom_map.get(value, value))
        result["unrepresented_electron_step"] = electron_step

    for field in ATOM_MAPPED_SMILES_FIELDS:
        value = result.get(field)
        if isinstance(value, str):
            result[field] = remap_smiles_atom_maps(value, atom_map)

    if namespace is not None:
        result["atom_map_namespace"] = namespace
    return result


def remap_smiles_atom_maps(
    smiles: str, atom_map: Mapping[int, int], *, canonical: bool = True
) -> str:
    """Remap bracket atom-map numbers in one SMILES fragment."""
    molecule = Chem.MolFromSmiles(smiles, sanitize=False)
    if molecule is None:
        raise ValueError(f"Could not parse mapped SMILES fragment: {smiles!r}")
    for atom in molecule.GetAtoms():
        current = int(atom.GetAtomMapNum())
        if current:
            atom.SetAtomMapNum(int(atom_map.get(current, current)))
    return Chem.MolToSmiles(molecule, canonical=canonical)


def remap_reactant_namespace(
    source_rsmi: str, target_rsmi: str, atom_map: Mapping[int, int]
) -> str:
    """Combine source reactant serialization with a target product namespace.

    Preserving source atom order retains its curated aromatic Kekulé choice for
    the electron-pushing editor, while the product and all atom-map numbers use
    the canonical target namespace.
    """
    source_reactants = source_rsmi.split(">>", 1)[0]
    target_products = target_rsmi.split(">>", 1)[1]
    reactants = remap_smiles_atom_maps(source_reactants, atom_map, canonical=False)
    return f"{reactants}>>{target_products}"


def representation_verification_rsmi(
    rsmi: str, representation: Mapping[str, Any] | None
) -> str:
    """Return the endpoint RSMI against which a pair-only EPD is verified.

    Exact representations are unchanged. A documented surrogate is generated
    by applying its formal-charge overrides to mapped product atoms. This is
    robust to component and SMILES traversal order and therefore also works for
    projected query reactions.
    """
    if not representation or representation.get("mode") in (None, "exact"):
        return rsmi
    overrides = representation.get("lwg_formal_charge_overrides")
    if not isinstance(overrides, Mapping) or not overrides:
        raise ValueError(
            "Non-exact EPD representations require lwg_formal_charge_overrides."
        )

    try:
        reactants, products = rsmi.split(">>", 1)
    except ValueError as exc:
        raise ValueError("Reaction SMILES must contain exactly one '>>'.") from exc
    molecule = Chem.MolFromSmiles(products, sanitize=False)
    if molecule is None:
        raise ValueError("Could not parse the mapped product side.")

    remaining = {int(key): int(value) for key, value in overrides.items()}
    for atom in molecule.GetAtoms():
        atom_map_number = int(atom.GetAtomMapNum())
        if atom_map_number in remaining:
            atom.SetFormalCharge(remaining.pop(atom_map_number))
    if remaining:
        raise ValueError(
            "Representation charge overrides reference product atom maps that "
            f"are absent: {sorted(remaining)}"
        )
    return f"{reactants}>>{Chem.MolToSmiles(molecule, canonical=True)}"
