#!/usr/bin/env python
"""Render reaction SMILES and ordered EPD arrows from SynEPD polar JSON.

The current refactored format is a schema-wrapped object such as::

    {
      "schema": "synepd.clean.polar.v1",
      "count": 1915,
      "label_policy": "Singular label fields are primary; plural fields include aliases.",
      "records": [...]
    }

This script also keeps backward compatibility with the older ``cases``,
``classes``, ``entries`` and ``items`` containers.

Depictions are drawn with the CDK Depict service by default (the same engine
the SynEPD web explorer uses), so the PDF matches the "2D Reaction Diagram"
panel: atom-map indices shown, groups fully explicit (no abbreviations), and a
selectable hydrogen-display mode. RDKit is used automatically when CDK Depict is
unreachable, and can be forced with ``--engine rdkit`` for fully offline runs.

Examples:
    python render_epd_pdf.py polar.json
    python render_epd_pdf.py polar.json --validate-only
    python render_epd_pdf.py polar.json --output-dir rendered --per-page 2
    python render_epd_pdf.py polar.json --hdisp all --no-atom-mapping
    python render_epd_pdf.py polar.json --engine rdkit          # offline
    python render_epd_pdf.py polar.json --cdk-base-url http://localhost:8080
"""

from __future__ import annotations

import argparse
import glob
import io
import json
import os
import sys
import textwrap
import urllib.parse
import urllib.request
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")  # select a headless backend before importing pyplot
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402

from rdkit import Chem  # noqa: E402
from rdkit.Chem import AllChem, rdChemReactions, rdDepictor  # noqa: E402
from rdkit.Chem.Draw import rdMolDraw2D  # noqa: E402

HERE = Path(__file__).resolve().parent
RED = (0.85, 0.0, 0.0)
SUPPORTED_SCHEMA_PREFIX = "synepd.clean.polar."

# The refactored clean schema uses ``epd``. The remaining fields are retained
# for older exports and evaluation files.
ARROW_FIELDS = (
    ("epd", "EPD"),
    ("ground_truth", "ground truth"),
    ("expected", "expected"),
)

PRIMARY_REQUIRED_FIELDS = (
    "id",
    "family",
    "tax_code",
    "entry_code",
    "reaction_name",
    "source_reaction_id",
    "mechanism_id",
    "mechanism_variant",
    "epd_order_variant",
    "rsmi",
    "epd",
)


# ----------------------------------------------------------- loading/schema ---
def _is_record_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, Mapping) for item in value)


def get_records(data: Any) -> list[dict[str, Any]]:
    """Return records from the refactored or legacy JSON container."""
    if isinstance(data, list):
        if not _is_record_list(data):
            raise ValueError("Top-level JSON list must contain objects")
        return [dict(item) for item in data]

    if not isinstance(data, Mapping):
        raise ValueError("Top-level JSON value must be an object or a list")

    # Refactored schema first, then known legacy containers.
    for key in ("records", "cases", "classes", "entries", "items"):
        value = data.get(key)
        if _is_record_list(value):
            return [dict(item) for item in value]

    # Conservative fallback for an older wrapper with exactly one list of
    # record-like objects. Avoid choosing arbitrary metadata lists.
    candidates = [value for value in data.values() if _is_record_list(value)]
    if len(candidates) == 1:
        return [dict(item) for item in candidates[0]]

    raise ValueError("Could not locate a record list in the JSON document")


def load_dataset(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    records = get_records(data)
    metadata = dict(data) if isinstance(data, Mapping) else {}
    metadata.pop("records", None)
    metadata.pop("cases", None)
    metadata.pop("classes", None)
    metadata.pop("entries", None)
    metadata.pop("items", None)
    return metadata, records


def validate_dataset(
    metadata: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    path: Path,
) -> list[str]:
    """Validate structural assumptions and return non-fatal warnings."""
    warnings: list[str] = []
    schema = metadata.get("schema")

    if schema and not str(schema).startswith(SUPPORTED_SCHEMA_PREFIX):
        warnings.append(f"unrecognized schema {schema!r}")

    declared_count = metadata.get("count")
    if declared_count is not None and declared_count != len(records):
        raise ValueError(
            f"{path.name}: declared count={declared_count}, "
            f"but loaded {len(records)} records"
        )

    ids: list[Any] = []
    missing: list[tuple[int, list[str]]] = []
    for index, rec in enumerate(records, start=1):
        if not isinstance(rec, Mapping):
            raise ValueError(f"{path.name}: record {index} is not an object")
        if rec.get("id") is not None:
            ids.append(rec.get("id"))

        if schema and str(schema).startswith(SUPPORTED_SCHEMA_PREFIX):
            absent = [key for key in PRIMARY_REQUIRED_FIELDS if key not in rec]
            if absent:
                missing.append((index, absent))

        _validate_plural_policy(rec, index, path)
        _validate_steps(rec, index, path)

    if missing:
        preview = "; ".join(
            f"record {index}: {', '.join(fields)}" for index, fields in missing[:5]
        )
        raise ValueError(f"{path.name}: missing required fields ({preview})")

    if len(ids) != len(set(ids)):
        raise ValueError(f"{path.name}: duplicate record IDs detected")

    return warnings


def _validate_plural_policy(rec: Mapping[str, Any], index: int, path: Path) -> None:
    for singular, plural in (
        ("tax_code", "tax_codes"),
        ("entry_code", "entry_codes"),
        ("reaction_name", "reaction_names"),
    ):
        primary = rec.get(singular)
        aliases = rec.get(plural)
        if aliases is None:
            continue
        if not isinstance(aliases, list):
            raise ValueError(
                f"{path.name}: record {index} field {plural!r} must be a list"
            )
        if primary not in (None, "") and aliases and aliases[0] != primary:
            raise ValueError(
                f"{path.name}: record {index} violates label policy: "
                f"{singular!r} must be the first value of {plural!r}"
            )


def _validate_steps(rec: Mapping[str, Any], index: int, path: Path) -> None:
    steps, _ = get_steps(rec)
    if steps is None:
        return
    if not isinstance(steps, list):
        raise ValueError(f"{path.name}: record {index} trajectory must be a list")
    for step_index, step in enumerate(steps, start=1):
        try:
            normalize_step(step)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{path.name}: record {index}, step {step_index}: {exc}"
            ) from exc


# --------------------------------------------------------------- accessors ---
def _as_nonempty_strings(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    result: list[str] = []
    for item in values:
        if item is None:
            continue
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def primary_label(
    rec: Mapping[str, Any],
    singular: str,
    plural: str,
    *legacy_fields: str,
) -> str:
    """Use the singular primary label, then plural/legacy fallbacks."""
    primary = _as_nonempty_strings(rec.get(singular))
    if primary:
        return primary[0]

    plural_values = _as_nonempty_strings(rec.get(plural))
    if plural_values:
        return plural_values[0]

    for field in legacy_fields:
        value = _as_nonempty_strings(rec.get(field))
        if value:
            return value[0]
    return ""


def rec_id(rec: Mapping[str, Any]) -> Any:
    for key in ("id", "case_id"):
        if rec.get(key) is not None:
            return rec[key]
    return None


def rec_code(rec: Mapping[str, Any]) -> str:
    return primary_label(
        rec,
        "entry_code",
        "entry_codes",
        "level4_code",
        "tax_code",
        "code",
    )


def rec_name(rec: Mapping[str, Any]) -> str:
    return primary_label(rec, "reaction_name", "reaction_names", "name")


def name_line(rec: Mapping[str, Any]) -> str:
    rid = rec_id(rec)
    code = rec_code(rec)
    name = rec_name(rec)

    pieces: list[str] = []
    if rid is not None:
        pieces.append(f"ID {rid}")
    elif code:
        pieces.append(f"ID {code}")

    if code and not (rid is None and pieces == [f"ID {code}"]):
        pieces.append(code)
    if name:
        pieces.append(name)
    return "  -  ".join(pieces) or "Unnamed record"


def alias_pairs(rec: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Return non-primary ``(entry_code, reaction_name)`` alias pairs."""
    codes = _as_nonempty_strings(rec.get("entry_codes"))
    names = _as_nonempty_strings(rec.get("reaction_names"))
    primary_code = rec_code(rec)
    primary_name = rec_name(rec)

    aliases: list[tuple[str, str]] = []
    for code, name in zip_longest(codes, names, fillvalue=""):
        if code == primary_code and name == primary_name:
            continue
        pair = (code, name)
        if pair != ("", "") and pair not in aliases:
            aliases.append(pair)
    return aliases


def alias_line(rec: Mapping[str, Any]) -> str:
    aliases = alias_pairs(rec)
    if not aliases:
        return ""
    formatted = [
        " - ".join(part for part in (code, name) if part) for code, name in aliases
    ]
    return "Aliases: " + "; ".join(formatted)


def get_steps(rec: Mapping[str, Any]) -> tuple[Any, str | None]:
    for key, label in ARROW_FIELDS:
        value = rec.get(key)
        if value:
            return value, label
    return None, None


def normalize_step(step: Any) -> tuple[str, list[Any], list[Any]]:
    """Normalize list-triplet or dict trajectory steps."""
    if isinstance(step, Mapping):
        arrow_type = step.get("type") or step.get("arrow_type") or step.get("action")
        source = step.get("source") or step.get("src") or step.get("from")
        target = step.get("target") or step.get("dst") or step.get("to")
    elif isinstance(step, (list, tuple)) and len(step) == 3:
        arrow_type, source, target = step
    else:
        raise ValueError("step must be a three-item sequence or an object")

    if not isinstance(arrow_type, str) or not arrow_type:
        raise TypeError("arrow type must be a non-empty string")
    if not isinstance(source, (list, tuple)):
        raise TypeError("source must be a list")
    if not isinstance(target, (list, tuple)):
        raise TypeError("target must be a list")
    return arrow_type, list(source), list(target)


# --------------------------------------------------------------- rendering ---
def prep(mol: Chem.Mol) -> Chem.Mol:
    """Move atom-map numbers into red annotation notes and lay out 2D coords.

    Explicit hydrogens carried in the SMILES are kept as real atoms so they are
    drawn; only the map number is relocated to a note for a clean depiction.
    """
    for atom in mol.GetAtoms():
        map_number = atom.GetAtomMapNum()
        if map_number:
            atom.SetProp("atomNote", str(map_number))
            atom.SetAtomMapNum(0)
    rdDepictor.Compute2DCoords(mol)
    return mol


def _hydrogen_preserving_params() -> Chem.SmilesParserParams:
    # removeHs=False keeps hydrogens that are explicit in the SMILES, mirroring
    # the SynEPD web depiction (synepd/web/server.py). This is what lets the
    # transferred protons in EPD reactions appear in the drawing.
    params = Chem.SmilesParserParams()
    params.removeHs = False
    params.sanitize = True
    return params


def _side_to_mol(side_smiles: str, params: Chem.SmilesParserParams) -> Chem.Mol:
    mol = Chem.MolFromSmiles(side_smiles, params)
    if mol is None:
        raise ValueError(f"RDKit could not parse fragment: {side_smiles}")
    return prep(mol)


def parse_reaction(rsmi: str):
    """Parse an ``R>>P`` reaction while preserving SMILES-explicit hydrogens.

    Each side is parsed as one (possibly multi-fragment) molecule with
    ``removeHs=False`` and assembled into a reaction, so explicit hydrogens are
    never silently dropped the way ``ReactionFromSmarts`` would. Unusual arrows
    (agents, no ``>>``) fall back to the standard reaction parser.
    """
    if not isinstance(rsmi, str) or not rsmi.strip():
        raise ValueError("no rsmi")

    sides = rsmi.split(">>")
    if len(sides) == 2 and sides[0].strip() and sides[1].strip():
        params = _hydrogen_preserving_params()
        reaction = rdChemReactions.ChemicalReaction()
        reaction.AddReactantTemplate(_side_to_mol(sides[0], params))
        reaction.AddProductTemplate(_side_to_mol(sides[1], params))
        return reaction

    # Fallback: let RDKit handle non-standard arrows; hydrogens may be implicit.
    reaction = AllChem.ReactionFromSmarts(rsmi, useSmiles=True)
    if reaction is None:
        raise ValueError("RDKit could not parse reaction SMILES")
    for mol in list(reaction.GetReactants()) + list(reaction.GetProducts()):
        Chem.SanitizeMol(mol)
        prep(mol)
    return reaction


def reaction_png(rsmi: str, size: tuple[int, int] = (1600, 520)) -> bytes:
    """Render a reaction to a high-resolution PNG with fully explicit atoms.

    Atoms and bonds are drawn explicitly (no condensed abbreviations), terminal
    methyls are labelled, and hydrogens present in the SMILES are shown.
    """
    reaction = parse_reaction(rsmi)
    drawer = rdMolDraw2D.MolDraw2DCairo(*size)
    options = drawer.drawOptions()
    # Fully explicit depiction: label terminal methyls, never abbreviate groups.
    options.explicitMethyl = True
    options.useBWAtomPalette()
    options.setAtomNoteColour(RED)
    options.annotationFontScale = 0.7
    options.bondLineWidth = 2
    options.minFontSize = 14
    options.maxFontSize = 24
    options.padding = 0.06
    drawer.DrawReaction(reaction)
    drawer.FinishDrawing()
    return drawer.GetDrawingText()


# ------------------------------------------------------------- CDK Depict ---
# The SynEPD web explorer depicts reactions with the CDK Depict service (see
# synepd/web/static/app.js: renderCDKDepict). These options mirror that panel:
# atom mapping (annotate=mapidx), abbreviations on/off, and a hydrogen-display
# mode. RDKit remains the offline fallback, exactly as in the web client.
CDK_DEFAULT_BASE_URL = "https://www.simolecule.com/cdkdepict"
CDK_HDISP_CHOICES = ("bridgehead", "stereo", "implicit", "all")
CDK_STYLE_CHOICES = ("cow", "cod")  # colour-on-white / colour-on-dark


@dataclass
class RenderOptions:
    """Depiction settings, mirroring the web explorer's CDK Depict controls."""

    engine: str = "cdk"  # "cdk" (with RDKit fallback) or "rdkit"
    base_url: str = CDK_DEFAULT_BASE_URL
    style: str = "cow"
    abbreviations: bool = False
    hdisp: str = "bridgehead"
    atom_mapping: bool = True
    zoom: float = 3.0
    timeout: float = 30.0


def cdk_depict_url(smiles: str, opts: RenderOptions, fmt: str = "png") -> str:
    """Build a CDK Depict request URL identical in shape to the web client's."""
    query = urllib.parse.urlencode(
        {
            "smi": smiles,
            "zoom": opts.zoom,
            "abbr": "on" if opts.abbreviations else "off",
            "hdisp": opts.hdisp,
            "showtitle": "false",
            "annotate": "mapidx" if opts.atom_mapping else "none",
        }
    )
    return f"{opts.base_url.rstrip('/')}/depict/{opts.style}/{fmt}?{query}"


_CDK_CACHE: dict[str, bytes] = {}


def cdk_depict_png(smiles: str, opts: RenderOptions) -> bytes:
    """Fetch a reaction/molecule PNG from CDK Depict; cache by request URL."""
    if not isinstance(smiles, str) or not smiles.strip():
        raise ValueError("no smiles")
    url = cdk_depict_url(smiles, opts, fmt="png")
    cached = _CDK_CACHE.get(url)
    if cached is not None:
        return cached
    request = urllib.request.Request(
        url, headers={"User-Agent": "synepd-render-epd-pdf"}
    )
    with urllib.request.urlopen(request, timeout=opts.timeout) as response:
        data = response.read()
    if not data.startswith(b"\x89PNG"):
        raise ValueError("CDK Depict did not return a PNG image")
    _CDK_CACHE[url] = data
    return data


def make_renderer(opts: RenderOptions) -> tuple[Callable[[str], bytes], dict]:
    """Return a ``rsmi -> PNG bytes`` renderer plus a mutable stats dict.

    For the ``cdk`` engine, a failed remote request falls back to the local
    RDKit depiction so a batch never aborts on a transient network error.
    """
    stats = {"cdk": 0, "rdkit": 0, "fallback": 0}

    if opts.engine == "rdkit":

        def render_rdkit(rsmi: str) -> bytes:
            stats["rdkit"] += 1
            return reaction_png(rsmi)

        return render_rdkit, stats

    def render_cdk(rsmi: str) -> bytes:
        try:
            data = cdk_depict_png(rsmi, opts)
            stats["cdk"] += 1
            return data
        except Exception as exc:
            stats["fallback"] += 1
            if stats["fallback"] <= 5:
                print(
                    f"warning: CDK Depict failed ({exc}); using RDKit fallback",
                    file=sys.stderr,
                )
            return reaction_png(rsmi)

    return render_cdk, stats


def _format_site(site: Iterable[Any]) -> str:
    return ",".join(map(str, site))


def representation_note(rec: Mapping[str, Any]) -> str:
    info = rec.get("epd_representation")
    if not isinstance(info, Mapping):
        return ""

    parts: list[str] = []
    mode = info.get("mode")
    if mode:
        parts.append(f"mode={mode}")
    limitation = info.get("limitation")
    if limitation:
        parts.append(str(limitation))
    unrepresented = info.get("unrepresented_electron_step")
    if isinstance(unrepresented, Mapping) and unrepresented.get("description"):
        parts.append("Unrepresented step: " + str(unrepresented["description"]))
    return "EPD representation note: " + " | ".join(parts) if parts else ""


def arrow_caption(rec: Mapping[str, Any], width: int = 132) -> str:
    steps, label = get_steps(rec)
    lines: list[str] = []

    if steps:
        parts: list[str] = []
        for index, raw_step in enumerate(steps, start=1):
            arrow_type, source, target = normalize_step(raw_step)
            arrow_type = arrow_type.replace("Sigma", "sigma").replace("Pi", "pi")
            parts.append(
                f"{index}) {arrow_type}: "
                f"{{{_format_site(source)}}}->{{{_format_site(target)}}}"
            )
        caption = f"Arrows (order, {label}): " + "    ".join(parts)
        lines.append(
            textwrap.fill(
                caption,
                width=width,
                subsequent_indent="    ",
                break_long_words=False,
                break_on_hyphens=False,
            )
        )
    else:
        reason = rec.get("unresolved_reason") or "no trajectory"
        error = str(rec.get("error") or "").strip()
        lines.append(f"No arrows - {reason}" + (f": {error}" if error else ""))

    reason = rec.get("unresolved_reason")
    if steps and reason:
        lines.append(f"Unresolved: {reason}")

    note = representation_note(rec)
    if note:
        lines.append(
            textwrap.fill(
                note,
                width=width,
                subsequent_indent="    ",
                break_long_words=False,
                break_on_hyphens=False,
            )
        )
    return "\n".join(lines)


def title_text(rec: Mapping[str, Any], show_aliases: bool, width: int = 105) -> str:
    title = name_line(rec)
    if show_aliases:
        aliases = alias_line(rec)
        if aliases:
            title += "\n" + textwrap.fill(
                aliases,
                width=width,
                subsequent_indent="    ",
                break_long_words=False,
                break_on_hyphens=False,
            )
    return title


def caption_font_size(rec: Mapping[str, Any]) -> float:
    steps, _ = get_steps(rec)
    count = len(steps) if isinstance(steps, list) else 0
    if count <= 6:
        return 8.5
    if count <= 12:
        return 7.5
    return 6.5


# --------------------------------------------------------------------- pdf ---
def build_reaction_pdf(
    records: Sequence[Mapping[str, Any]],
    out: Path,
    per_page: int = 4,
    show_aliases: bool = True,
    render_png: Callable[[str], bytes] | None = None,
) -> None:
    if per_page < 1:
        raise ValueError("per_page must be at least 1")
    if render_png is None:
        render_png = reaction_png

    with PdfPages(out) as pdf:
        for start in range(0, len(records), per_page):
            chunk = records[start : start + per_page]
            fig, axes_obj = plt.subplots(per_page, 1, figsize=(8.27, 11.69))
            axes = [axes_obj] if per_page == 1 else list(axes_obj)

            for axis, rec in zip(axes, chunk):
                rsmi = rec.get("rsmi")
                try:
                    png = render_png(str(rsmi or ""))
                    axis.imshow(plt.imread(io.BytesIO(png), format="png"))
                except Exception as exc:  # keep the rest of the batch renderable
                    axis.text(
                        0.5,
                        0.6,
                        f"[no depiction]  {rsmi or ''}\n{exc}",
                        transform=axis.transAxes,
                        ha="center",
                        va="center",
                        fontsize=8,
                        color="#990000",
                        family="monospace",
                        wrap=True,
                    )

                axis.set_title(
                    title_text(rec, show_aliases=show_aliases),
                    fontsize=10,
                    loc="left",
                    pad=6,
                )
                axis.text(
                    0.0,
                    -0.04,
                    arrow_caption(rec),
                    transform=axis.transAxes,
                    va="top",
                    ha="left",
                    fontsize=caption_font_size(rec),
                    color="#003366",
                    family="monospace",
                )
                axis.axis("off")

            for axis in axes[len(chunk) :]:
                axis.axis("off")

            fig.tight_layout(h_pad=2.5)
            pdf.savefig(fig, bbox_inches="tight")
            plt.close(fig)


def build_list_pdf(
    records: Sequence[Mapping[str, Any]],
    out: Path,
    title: str,
    per_page: int = 30,
    show_aliases: bool = True,
) -> None:
    """Build a compact ID/name list for class-only files without RSMI."""
    if per_page < 1:
        raise ValueError("per_page must be at least 1")

    with PdfPages(out) as pdf:
        for start in range(0, len(records), per_page):
            chunk = records[start : start + per_page]
            fig = plt.figure(figsize=(8.27, 11.69))
            axis = fig.add_axes([0.06, 0.04, 0.9, 0.9])
            axis.axis("off")
            axis.text(0, 1.0, title, fontsize=13, weight="bold", va="top")
            y = 0.95
            for rec in chunk:
                sample_count = rec.get("sample_count")
                note = (
                    f"   (sample_count={sample_count})"
                    if sample_count is not None
                    else ""
                )
                line = name_line(rec) + note
                if show_aliases and alias_line(rec):
                    line += " | " + alias_line(rec)
                axis.text(
                    0,
                    y,
                    textwrap.fill(line, width=118, subsequent_indent="    "),
                    fontsize=9,
                    va="top",
                    family="monospace",
                )
                y -= 1.0 / (per_page + 2)
            pdf.savefig(fig)
            plt.close(fig)


# -------------------------------------------------------------- processing ---
def validate_reactions(records: Sequence[Mapping[str, Any]]) -> list[str]:
    failures: list[str] = []
    for rec in records:
        rsmi = rec.get("rsmi")
        if not rsmi:
            continue
        try:
            parse_reaction(str(rsmi))
        except Exception as exc:
            failures.append(f"ID {rec_id(rec)} ({rec_code(rec)}): {exc}")
    return failures


def process(
    path: Path,
    *,
    output_dir: Path | None,
    per_page: int,
    show_aliases: bool,
    validate_only: bool,
    limit: int | None,
    render_options: RenderOptions | None = None,
) -> Path | None:
    render_options = render_options or RenderOptions()
    metadata, all_records = load_dataset(path)
    warnings = validate_dataset(metadata, all_records, path)
    for warning in warnings:
        print(f"warning: {path.name}: {warning}", file=sys.stderr)

    records = all_records[:limit] if limit is not None else all_records
    schema = metadata.get("schema", "legacy/unspecified")

    if validate_only:
        failures = validate_reactions(records)
        if failures:
            preview = "\n".join(f"  - {failure}" for failure in failures[:20])
            extra = "" if len(failures) <= 20 else f"\n  ... {len(failures) - 20} more"
            raise ValueError(
                f"{path.name}: {len(failures)} RDKit validation failures:\n"
                f"{preview}{extra}"
            )
        print(
            f"{path.name:38s} {len(records):5d}/{len(all_records):5d} records "
            f"validated [{schema}]"
        )
        return None

    destination = output_dir or path.resolve().parent
    destination.mkdir(parents=True, exist_ok=True)
    out = destination / f"{path.stem}_rsmi.pdf"
    has_rsmi = any(rec.get("rsmi") for rec in records)
    mode = "reaction" if has_rsmi else "list"
    engine = render_options.engine if has_rsmi else "n/a"
    print(
        f"{path.name:38s} {len(records):5d}/{len(all_records):5d} records "
        f"[{mode}; {schema}; engine={engine}] -> {out.name}"
    )

    if has_rsmi:
        render_png, stats = make_renderer(render_options)
        build_reaction_pdf(
            records,
            out,
            per_page=per_page,
            show_aliases=show_aliases,
            render_png=render_png,
        )
        if render_options.engine == "cdk":
            print(
                f"  depiction: {stats['cdk']} via CDK Depict, "
                f"{stats['fallback']} RDKit fallback",
                file=sys.stderr,
            )
    else:
        build_list_pdf(
            records,
            out,
            f"{path.stem} - class-level nodes (no rsmi)",
            show_aliases=show_aliases,
        )
    return out


def discover_default_files() -> list[Path]:
    return [Path(path) for path in sorted(glob.glob(str(HERE / "polar*.json")))]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render SynEPD polar JSON records to PDF with RDKit."
    )
    parser.add_argument(
        "files",
        nargs="*",
        type=Path,
        help="JSON files; defaults to polar*.json beside this script",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="directory for generated PDFs; defaults to each input file's directory",
    )
    parser.add_argument(
        "--per-page",
        type=int,
        default=4,
        help="reaction records per PDF page (default: 4)",
    )
    parser.add_argument(
        "--hide-aliases",
        action="store_true",
        help="show only singular primary labels, omitting plural-field aliases",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="validate schema, EPD steps and RDKit parsing without writing a PDF",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="process only the first N records (useful for a quick preview)",
    )

    depict = parser.add_argument_group("depiction (mirrors the web CDK Depict panel)")
    depict.add_argument(
        "--engine",
        choices=("cdk", "rdkit"),
        default="cdk",
        help="depiction engine: 'cdk' (CDK Depict, RDKit fallback) or 'rdkit' "
        "(default: cdk)",
    )
    depict.add_argument(
        "--cdk-base-url",
        default=CDK_DEFAULT_BASE_URL,
        help=f"CDK Depict base URL, e.g. a self-hosted instance "
        f"(default: {CDK_DEFAULT_BASE_URL})",
    )
    depict.add_argument(
        "--style",
        choices=CDK_STYLE_CHOICES,
        default="cow",
        help="CDK colour style: cow (on white) or cod (on dark) (default: cow)",
    )
    depict.add_argument(
        "--abbreviations",
        action="store_true",
        help="condense common groups into abbreviations (default: off, fully explicit)",
    )
    depict.add_argument(
        "--hdisp",
        choices=CDK_HDISP_CHOICES,
        default="bridgehead",
        help="CDK hydrogen display mode (default: bridgehead)",
    )
    depict.add_argument(
        "--no-atom-mapping",
        action="store_true",
        help="hide atom-map indices (default: show them, annotate=mapidx)",
    )
    depict.add_argument(
        "--zoom",
        type=float,
        default=3.0,
        help="CDK Depict zoom / resolution factor (default: 3.0)",
    )

    args = parser.parse_args(argv)
    if args.per_page < 1:
        parser.error("--per-page must be at least 1")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.zoom <= 0:
        parser.error("--zoom must be positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    files = args.files or discover_default_files()
    if not files:
        print("No input JSON files found.", file=sys.stderr)
        return 1

    render_options = RenderOptions(
        engine=args.engine,
        base_url=args.cdk_base_url,
        style=args.style,
        abbreviations=args.abbreviations,
        hdisp=args.hdisp,
        atom_mapping=not args.no_atom_mapping,
        zoom=args.zoom,
    )

    failures = 0
    for file_path in files:
        try:
            process(
                file_path,
                output_dir=args.output_dir,
                per_page=args.per_page,
                show_aliases=not args.hide_aliases,
                validate_only=args.validate_only,
                limit=args.limit,
                render_options=render_options,
            )
        except Exception as exc:
            failures += 1
            print(f"error: {file_path}: {exc}", file=sys.stderr)

    if failures:
        print(f"failed: {failures} file(s)", file=sys.stderr)
        return 1
    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
