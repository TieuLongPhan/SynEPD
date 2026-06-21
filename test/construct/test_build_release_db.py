import sqlite3
import tempfile
from pathlib import Path

from synepd.construct.build_release_db import (
    build_release_database,
    generate_aam_key,
    extract_reaction_name,
)


def test_build_release_database():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_release.sqlite"
        build_release_database(
            json_path=Path("data/polar.json"),
            hierarchy_path=Path("data/hierarchy.md"),
            db_path=db_path,
        )

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Check Taxon
        cursor.execute("SELECT COUNT(*) FROM taxon;")
        assert cursor.fetchone()[0] > 0

        # Check Reaction
        cursor.execute("SELECT COUNT(*) FROM reaction;")
        rxn_count = cursor.fetchone()[0]
        assert rxn_count > 0

        # Check specific reaction name
        cursor.execute(
            "SELECT name FROM reaction WHERE case_id = 'polar01_001_alcohol_protonation_deprotonation';"
        )
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == "Alcohol protonation deprotonation"

        # Check Molecule
        cursor.execute("SELECT COUNT(*) FROM molecule;")
        assert cursor.fetchone()[0] > 0

        # Check EPD
        cursor.execute("SELECT COUNT(*) FROM epd;")
        assert cursor.fetchone()[0] > 0

        # Check specific Arrow
        cursor.execute(
            "SELECT COUNT(*) FROM epd_arrow WHERE arrow_type_code = 'LP-/Sigma+';"
        )
        assert cursor.fetchone()[0] > 0

        conn.close()


def test_extract_reaction_name():
    # Verify exact match with normal names
    assert (
        extract_reaction_name("polar01_001_alcohol_protonation_deprotonation")
        == "Alcohol protonation deprotonation"
    )
    # Verify spelling fixes
    assert (
        extract_reaction_name("polar01_001_alcohol_protonation_deprptonation")
        == "Alcohol protonation deprotonation"
    )
    assert (
        extract_reaction_name("polar01_020_nitro_aci_nitro_tautomerizaton")
        == "Nitro aci nitro tautomerization"
    )
    # Verify workup suffix removal and direct mapping
    assert (
        extract_reaction_name(
            "polar06_699_dissolving_metal_carbonyl_reduction_polar_workup"
        )
        == "Alcohol protonation deprotonation"
    )
    assert (
        extract_reaction_name("polar08_864_acyloin_condensation_polar_workup_sequence")
        == "Acyloin condensation"
    )
    # Verify fallback behavior for short case IDs
    assert extract_reaction_name("simple_case") == "Simple case"
