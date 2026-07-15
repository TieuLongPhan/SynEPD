from pathlib import Path

from synepd.database import ReleaseRepository, SQLiteReleaseRepository

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_release_repository_returns_typed_mechanism_records():
    with SQLiteReleaseRepository(REPOSITORY_ROOT / "data" / "epdb.sqlite") as repo:
        assert isinstance(repo, ReleaseRepository)
        reaction = repo.get_reaction(1)
        arrows = repo.get_arrows(1)
        context = repo.get_mechanism_context(1)

    assert reaction is not None
    assert reaction.aam_key
    assert reaction.canonical_aam_key
    assert arrows
    assert [arrow.index for arrow in arrows] == list(range(1, len(arrows) + 1))
    assert context is not None
    assert len(context.context_hash) == 64
    assert context.anchor_graph.number_of_nodes() > 0
    assert context.events


def test_release_repository_reports_missing_records():
    with SQLiteReleaseRepository(REPOSITORY_ROOT / "data" / "epdb.sqlite") as repo:
        assert repo.get_reaction(-1) is None
        assert repo.get_arrows(-1) == ()
        assert repo.get_mechanism_context(-1) is None
