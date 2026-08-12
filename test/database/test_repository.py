from pathlib import Path

from synepd.database import ReleaseDatabase, ReleaseRepository, SQLiteReleaseRepository

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_release_repository_returns_typed_mechanism_records():
    with SQLiteReleaseRepository(REPOSITORY_ROOT / "data" / "epdb.sqlite") as repo:
        assert isinstance(repo, ReleaseRepository)
        reaction = repo.get_reaction(1)
        arrows = repo.get_arrows(1)
        context = repo.get_mechanism_context(1)
        mc = repo.get_mechanistic_center(1)
        xrefs = repo.get_taxon_xrefs("POLAR.02.01.020")

    assert reaction is not None
    assert reaction.aam_key
    assert reaction.canonical_aam_key
    assert arrows
    assert [arrow.index for arrow in arrows] == list(range(1, len(arrows) + 1))
    assert context is not None
    assert len(context.context_hash) == 64
    assert context.anchor_graph.number_of_nodes() > 0
    assert context.events
    assert mc is not None
    assert mc.template_graph.number_of_nodes() > 0
    assert mc.transition_edge_count >= mc.rc_extension_edge_count
    assert any(xref.ontology_id == "RXNO:0000034" for xref in xrefs)


def test_release_repository_reports_missing_records():
    with SQLiteReleaseRepository(REPOSITORY_ROOT / "data" / "epdb.sqlite") as repo:
        assert repo.get_reaction(-1) is None
        assert repo.get_arrows(-1) == ()
        assert repo.get_mechanism_context(-1) is None
        assert repo.get_mechanistic_center(-1) is None
        assert repo.get_taxon_xrefs("POLAR.99.99.999") == ()


def test_reaction_xrefs_include_direct_and_ancestor_provenance(tmp_path):
    database_path = tmp_path / "release.sqlite"
    (tmp_path / "rxno_crosswalk.tsv").write_text(
        "taxon_code\ttaxon_level\ttaxon_name\trelation\tmatch_type\tscore\t"
        "ontology_id\tontology_name\n"
        "POLAR.02\t2\tSubstitution\tskos:exactMatch\ttoken\t0.97\t"
        "MOP:0000790\tsubstitution reaction\n"
        "POLAR.02.01.020\t4\tMitsunobu reaction\tskos:exactMatch\tcurated\t"
        "1.0\tRXNO:0000034\tMitsunobu reaction\n",
        encoding="utf-8",
    )
    with ReleaseDatabase(database_path) as database:
        database.create_tables()
        with database.connection:
            database.connection.executemany(
                "INSERT INTO taxon (code, parent_code, level, name) VALUES (?, ?, ?, ?)",
                (
                    ("POLAR", None, 1, "Polar"),
                    ("POLAR.02", "POLAR", 2, "Substitution"),
                    ("POLAR.02.01", "POLAR.02", 3, "SN2"),
                    (
                        "POLAR.02.01.020",
                        "POLAR.02.01",
                        4,
                        "Mitsunobu reaction",
                    ),
                    (
                        "POLAR.08.03.009",
                        None,
                        4,
                        "Mitsunobu activation stage",
                    ),
                ),
            )
            reaction_id = database.connection.execute(
                "INSERT INTO reaction (case_id, canonical_rsmi, aam_key) VALUES (?, ?, ?)",
                ("polar_000001", "CO>>CO", "[CH3:1][OH:2]>>[CH3:1][OH:2]"),
            ).lastrowid
            database.connection.executemany(
                "INSERT INTO reaction_entry_code VALUES (?, ?, ?, ?, ?)",
                (
                    (
                        reaction_id,
                        "POLAR.02.01.020",
                        "POLAR.02.01.020.001",
                        0,
                        1,
                    ),
                    (
                        reaction_id,
                        "POLAR.08.03.009",
                        "POLAR.08.03.009.001",
                        1,
                        0,
                    ),
                ),
            )
            database.connection.execute(
                "INSERT INTO reaction_taxonomy VALUES (?, ?)",
                (reaction_id, "POLAR.02.01.020"),
            )

    with SQLiteReleaseRepository(database_path) as repository:
        xrefs = repository.get_reaction_xrefs(reaction_id)
        releases = repository.get_ontology_releases()
        entry_codes = repository.get_reaction_entry_codes(reaction_id)

    assert [(xref.ontology_id, xref.inheritance_depth) for xref in xrefs] == [
        ("MOP:0000790", 2),
        ("RXNO:0000034", 0),
    ]
    assert xrefs[0].inherited
    assert not xrefs[1].inherited
    assert releases[0].id == "rxno-2021-12-16"
    assert [code.entry_code for code in entry_codes] == [
        "POLAR.02.01.020.001",
        "POLAR.08.03.009.001",
    ]
    assert [code.taxon_code for code in entry_codes] == [
        "POLAR.02.01.020",
        "POLAR.08.03.009",
    ]
    assert entry_codes[0].is_primary
    assert not entry_codes[1].is_primary
