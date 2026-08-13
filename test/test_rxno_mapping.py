from pathlib import Path
import sqlite3

import pytest

from scripts.build_rxno_mapping import (
    ensure_obo,
    load_obo,
    load_overrides,
    render_skos,
    validate_redirects,
)
from synepd.database.models import ReleaseDatabase
from synepd.linkage import load_rxno_linkage


def test_curated_rxno_overrides_resolve_against_active_sources():
    repo = Path(__file__).resolve().parents[1]
    terms = load_obo(repo / "data" / "rxno.obo")
    taxa = []
    for line in (
        (repo / "data" / "hierarchy.md").read_text(encoding="utf-8").splitlines()
    ):
        if " — " not in line:
            continue
        heading, name = line.split(" — ", 1)
        code = heading.lstrip("# ")
        if code.startswith("POLAR"):
            taxa.append((code, code.count("."), name))
    overrides = load_overrides(
        repo / "data" / "rxno_mapping_overrides.tsv", taxa, terms
    )
    assert overrides
    assert {row[4] for row in overrides} == {"curated"}


def test_curated_rxno_overrides_reject_inactive_taxa(tmp_path):
    path = tmp_path / "overrides.tsv"
    path.write_text(
        "taxon_code\trelation\tontology_id\trationale\treview_status\n"
        "POLAR.99.99.999\tskos:exactMatch\tRXNO:0000034\tTest.\taccepted\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="inactive taxon"):
        load_overrides(
            path,
            [("POLAR", 0, "Polar")],
            [{"id": "RXNO:0000034", "name": "Mitsunobu reaction"}],
        )


def test_crosswalk_rows_remain_external_to_release_database(tmp_path):
    db_path = tmp_path / "release.sqlite"
    with ReleaseDatabase(db_path) as database:
        database.create_tables()
        with database.connection:
            database.connection.execute(
                "INSERT INTO taxon (code, level, name) VALUES (?, ?, ?)",
                ("POLAR.02.01.020", 4, "Mitsunobu reaction"),
            )
    (tmp_path / "rxno_crosswalk.tsv").write_text(
        "taxon_code\ttaxon_level\ttaxon_name\trelation\tmatch_type\tscore\t"
        "ontology_id\tontology_name\n"
        "POLAR.02.01.020\t4\tMitsunobu reaction\tskos:exactMatch\tcurated\t"
        "1.0\tRXNO:0000034\tMitsunobu reaction\n",
        encoding="utf-8",
    )
    xrefs, release = load_rxno_linkage(db_path)
    assert xrefs["POLAR.02.01.020"][0]["ontology_id"] == "RXNO:0000034"
    assert release and release["id"] == "rxno-2021-12-16"
    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {"ontology_release", "taxon_xref"}.isdisjoint(tables)


def test_pinned_obo_fails_closed_when_modified(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    snapshot = tmp_path / "rxno.obo"
    snapshot.write_bytes((repo / "data" / "rxno.obo").read_bytes())
    assert ensure_obo(snapshot) == snapshot

    snapshot.write_bytes(snapshot.read_bytes() + b"\n# modified\n")
    with pytest.raises(ValueError, match="checksum mismatch"):
        ensure_obo(snapshot)


def test_redirects_validate_retired_sources_and_active_targets(tmp_path):
    redirects = tmp_path / "redirects.tsv"
    redirects.write_text(
        "retired_taxon_code\treplacement_taxon_code\trelation\trationale\n"
        "POLAR.03.01.023\tPOLAR.03.03.002\treplaced_by\tMerged concept.\n",
        encoding="utf-8",
    )
    taxa = [("POLAR.03.03.002", 4, "Mukaiyama aldol addition")]
    assert validate_redirects(redirects, taxa) == [
        (
            "POLAR.03.01.023",
            "POLAR.03.03.002",
            "replaced_by",
            "Merged concept.",
        )
    ]

    with pytest.raises(ValueError, match="remains assigned"):
        validate_redirects(
            redirects,
            taxa,
            reaction_taxa={"POLAR.03.01.023"},
        )


def test_turtle_emits_linkset_provenance_and_is_part_of():
    accepted = [
        (
            "POLAR.08.03.009",
            4,
            "Mitsunobu activation stage",
            "dcterms:isPartOf",
            "curated",
            1.0,
            "RXNO:0000034",
            "Mitsunobu reaction",
        )
    ]
    ttl = render_skos(accepted, "releases/2021-12-16")
    assert "a void:Linkset" in ttl
    assert "dcterms:source" in ttl
    assert "dcterms:license" in ttl
    assert "dcterms:isPartOf obo:RXNO_0000034" in ttl
