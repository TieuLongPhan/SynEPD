from synepd.models import Case
from synepd.database.core import SynEPDDatabase
from synepd.query.filters import (
    by_level,
    by_template_pool,
    find_cases,
    search_labels,
    Query,
)


def _case(case_id: str) -> Case:
    return Case.from_dict(
        {
            "case_id": case_id,
            "dataset_name": "SynEPD",
            "schema_version": "0.1.0",
            "level1_code": "POLAR",
            "level1_name": "Polar Reactions",
            "level2_code": "POLAR.01",
            "level2_name": "Proton Transfer",
            "level3_code": "POLAR.01.01",
            "level3_name": "Heteroatom proton transfer",
            "level4_code": "POLAR.01.01.001",
            "level4_label": "Alcohol protonation",
            "case_variant": 1,
            "reaction_smiles": "CC[O-].[NH4+]>>CCO",
            "reaction_center_signature": "SIGMA",
            "reaction_center_template_pool": "pool1",
            "shares_reaction_center_within_level4": False,
            "atom_mapping": {},
            "validation_status": "VALID",
            "curation_status": "APPROVED",
            "manual_review_required": False,
        }
    )


def test_composable_filters():
    case = _case("C1")
    db = SynEPDDatabase((case,), summary={"case_count": 1})

    assert by_level(db, 1, "POLAR") == (case,)
    assert by_level(db, 2, "POLAR.01") == (case,)
    assert by_template_pool(db, "pool1") == (case,)

    assert find_cases(db, level1="POLAR", template_pool="pool1") == (case,)
    assert len(find_cases(db, text="Alcohol")) == 1

    matches = search_labels(db, "Proton")
    assert len(matches) > 0

    q = Query(db)
    assert q.filter(level1="POLAR").count() == 1
