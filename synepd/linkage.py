"""Optional external ontology linkage for the SynEPD taxonomy.

The release database intentionally stores only SynEPD-owned chemistry and
taxonomy.  RXNO/MOP mappings remain in the versioned crosswalk artifact and are
loaded on demand by this module.
"""

from __future__ import annotations

import csv
from functools import lru_cache
import hashlib
import os
from pathlib import Path
from typing import Iterable

ONTOLOGY_RELEASE = {
    "id": "rxno-2021-12-16",
    "ontology_iri": "http://purl.obolibrary.org/obo/rxno.owl",
    "version_iri": "http://purl.obolibrary.org/obo/rxno/releases/2021-12-16/rxno.owl",
    "data_version": "releases/2021-12-16",
    "license_iri": "http://creativecommons.org/licenses/by/4.0/",
}


def resolve_crosswalk_path(database: str | Path | None = None) -> Path:
    """Resolve the optional RXNO crosswalk without coupling it to SQLite."""
    configured = os.environ.get("SYNEPD_RXNO_CROSSWALK")
    if configured:
        return Path(configured)
    if database and not str(database).startswith(
        ("postgresql://", "postgres://", "host=")
    ):
        adjacent = Path(database).parent / "rxno_crosswalk.tsv"
        if adjacent.is_file():
            return adjacent
    return Path(__file__).resolve().parents[1] / "data" / "rxno_crosswalk.tsv"


@lru_cache(maxsize=8)
def _load_crosswalk(
    resolved_path: str, size: int, modified_ns: int
) -> tuple[dict[str, tuple[dict[str, object], ...]], dict[str, object]]:
    del size, modified_ns  # cache-key inputs; content is read from resolved_path
    path = Path(resolved_path)
    by_taxon: dict[str, list[dict[str, object]]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            by_taxon.setdefault(row["taxon_code"], []).append(
                {
                    "ontology_id": row["ontology_id"],
                    "relation": row["relation"],
                    "match_type": row["match_type"],
                    "score": float(row["score"]),
                    "name": row["ontology_name"],
                    "ontology_release_id": ONTOLOGY_RELEASE["id"],
                }
            )
    release = dict(ONTOLOGY_RELEASE)
    release["source_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return (
        {code: tuple(rows) for code, rows in by_taxon.items()},
        release,
    )


def load_rxno_linkage(
    database: str | Path | None = None,
) -> tuple[dict[str, tuple[dict[str, object], ...]], dict[str, object] | None]:
    """Return crosswalk rows keyed by POLAR taxon and their source metadata."""
    path = resolve_crosswalk_path(database)
    if not path.is_file():
        return {}, None
    stat = path.stat()
    return _load_crosswalk(str(path.resolve()), stat.st_size, stat.st_mtime_ns)


def resolve_lineage_xrefs(
    lineage: Iterable[tuple[str, str, int]],
    database: str | Path | None = None,
) -> list[dict[str, object]]:
    """Project external links onto assigned taxa through their POLAR ancestry."""
    by_taxon, _ = load_rxno_linkage(database)
    resolved: list[dict[str, object]] = []
    seen: set[tuple[str, str, int, str]] = set()
    for assigned_code, mapping_code, depth in lineage:
        for xref in by_taxon.get(mapping_code, ()):
            key = (assigned_code, mapping_code, int(depth), str(xref["ontology_id"]))
            if key in seen:
                continue
            seen.add(key)
            resolved.append(
                {
                    "assigned_taxon_code": assigned_code,
                    "mapping_taxon_code": mapping_code,
                    "inheritance_depth": int(depth),
                    "inherited": int(depth) > 0,
                    **xref,
                }
            )
    return sorted(
        resolved,
        key=lambda row: (
            str(row["ontology_id"]),
            int(row["inheritance_depth"]),
            str(row["assigned_taxon_code"]),
            str(row["mapping_taxon_code"]),
        ),
    )
