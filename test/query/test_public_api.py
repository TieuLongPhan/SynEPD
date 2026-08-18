"""Tests for the unified :mod:`synepd.query` namespace."""

from pathlib import Path
from unittest import mock

import pytest
import synepd

from synepd.query import (
    EPDManager,
    MechanismManager,
    MoleculeManager,
    ReactionManager,
    SynEPDQuery,
    TaxonomyManager,
    find_reactions_by_template,
    query_epd_by_reaction,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_release_query_entry_points_are_public():
    assert synepd.SynEPDQuery is SynEPDQuery
    assert callable(query_epd_by_reaction)
    assert callable(find_reactions_by_template)
    assert all(
        manager is not None
        for manager in (
            ReactionManager,
            MoleculeManager,
            TaxonomyManager,
            EPDManager,
            MechanismManager,
        )
    )


def test_high_level_release_query_scenarios():
    with SynEPDQuery(REPOSITORY_ROOT / "data" / "epdb.sqlite") as query:
        reaction = query.reaction("polar_001321")
        assert reaction is not None
        assert reaction["name"] == "O-Acylation of alcohols"
        assert {row["case_id"] for row in query.search_reactions("Mitsunobu")} == {
            "polar_001834",
            "polar_001949",
        }

        assert query.reactions_for_molecule("CCO", role="reactant")
        assert query.reactions_in_taxon("POLAR.04.01.012")
        assert query.reactions_matching_taxonomy("acylation")
        assert query.taxonomy_path("POLAR.04.01.012")[-1]["name"] == (
            "O-Acylation of alcohols"
        )
        assert query.reactions_by_arrow_count(2)
        assert query.reactions_containing_arrow("LP-/Sigma+")
        assert query.reactions_by_arrow_sequence(["LP-/Sigma+", "Sigma-/LP+"])

        mechanism = query.mechanism("polar_001321")
        assert mechanism is not None
        assert mechanism["its"] is not None
        assert mechanism["rc"] is not None
        assert mechanism["mc"] is not None
        assert mechanism["arrows"]
        assert query.reaction_xrefs("polar_001321")


def test_high_level_release_query_validates_user_choices():
    with SynEPDQuery(REPOSITORY_ROOT / "data" / "epdb.sqlite") as query:
        with pytest.raises(ValueError, match="role must be"):
            query.reactions_for_molecule("CCO", role="catalyst")
        with pytest.raises(ValueError, match="exactly one"):
            query.template_neighbors()
        with pytest.raises(ValueError, match="exactly one"):
            query.template_neighbors(
                case_id="polar_001321",
                template="C>>C",
            )


def test_high_level_remote_release_defaults_to_v041():
    db_path = REPOSITORY_ROOT / "data" / "epdb.sqlite"
    with mock.patch(
        "synepd.query.release.get_default_db_path", return_value=db_path
    ) as resolve:
        with SynEPDQuery.from_release() as query:
            assert query.reaction("polar_001321") is not None

    resolve.assert_called_once_with(version="0.4.1", source="auto", force=False)
