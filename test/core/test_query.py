import tempfile
from pathlib import Path
from synepd.core.query import find_reactions_by_template, query_epd_by_reaction
from synepd.construct.build_release_db import build_release_database


def test_query_and_template_search():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_query.sqlite"
        build_release_database(
            json_path=Path("data/polar.json"),
            hierarchy_path=Path("data/hierarchy.md"),
            db_path=db_path,
        )

        # 1. Test querying a reaction that exists in the DB (Path 1)
        res = query_epd_by_reaction("CC[O-].[NH4+]>>CCO.N", db_path)
        assert res["success"]
        assert res["path"] == 1
        assert len(res["arrows"]) > 0

        # 2. Test querying an imbalanced reaction (Path 2)
        res_imbalanced = query_epd_by_reaction("CC[O-]>>CCO", db_path)
        assert res_imbalanced["success"]
        assert res_imbalanced["balanced_from_imbalanced"]
        assert len(res_imbalanced["arrows"]) > 0

        # 3. Test find_reactions_by_template
        # Must be a mapped reaction SMILES to extract template center
        mapped_template = "[CH3:1][CH2:2][O-:3].[N+:4]([H:5])([H:6])([H:7])[H:8]>>[CH3:1][CH2:2][O:3][H:5].[N:4]([H:6])([H:7])[H:8]"
        reactions = find_reactions_by_template(mapped_template, db_path)
        assert len(reactions) > 0
        assert reactions[0]["case_id"].startswith("polar")
