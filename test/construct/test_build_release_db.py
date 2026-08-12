import sqlite3
import tempfile
import json
from pathlib import Path
from types import SimpleNamespace

import networkx as nx
import pytest
import synepd.construct.build_release_db as build_mod

from synepd.construct.build_release_db import (
    build_release_database,
    extract_reaction_name,
    reaction_centers_are_isomorphic,
    validate_clean_v2_payload,
)


def test_build_release_database_with_clean_records(monkeypatch):
    class FakeStandardize:
        def fit(self, rsmi):
            return rsmi

    class FakeCanon:
        def __init__(self, *args, **kwargs):
            pass

        def canonicalise(self, rsmi):
            return SimpleNamespace(canonical_rsmi=rsmi)

    def ok_balance(*args, **kwargs):
        return SimpleNamespace(balanced=True)

    def ok_atom_map_balance(*args, **kwargs):
        return SimpleNamespace(is_balanced=True)

    def ok_h_completion(*args, **kwargs):
        return True, None

    def fake_extract_graphs(*args, **kwargs):
        graph = nx.Graph()
        graph.add_node(1, element="C", charge=0, atom_map=1)
        graph.add_node(2, element="O", charge=0, atom_map=2)
        graph.add_edge(1, 2, order=(1, 1), standard_order=0)
        return graph, graph.copy(), "fake-wlhash"

    class FakeEditor:
        def apply(self, *args, **kwargs):
            return SimpleNamespace(step_reports=(), matches_product=True)

    monkeypatch.setattr(build_mod, "Standardize", FakeStandardize)
    monkeypatch.setattr(build_mod, "CanonRSMI", FakeCanon)
    monkeypatch.setattr(build_mod, "check_reaction_balance", ok_balance)
    monkeypatch.setattr(build_mod, "check_atom_map_balance", ok_atom_map_balance)
    monkeypatch.setattr(build_mod, "check_single_h_completion", ok_h_completion)
    monkeypatch.setattr(build_mod, "extract_graphs", fake_extract_graphs)
    monkeypatch.setattr(build_mod, "LWGEditor", FakeEditor)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        json_path = tmp / "polar_clean.json"
        hierarchy_path = tmp / "polar_hierarchy.json"
        db_path = Path(tmpdir) / "test_release.sqlite"
        json_path.write_text(
            json.dumps(
                {
                    "schema": "synepd.clean.polar.v2",
                    "count": 2,
                    "id_start": 1,
                    "records": [
                        {
                            "id": 1,
                            "family": "polar",
                            "tax_code": "POLAR.01.01.001",
                            "tax_codes": ["POLAR.01.01.001", "POLAR.01.01.002"],
                            "entry_code": "POLAR.01.01.001.001",
                            "entry_codes": [
                                "POLAR.01.01.001.001",
                                "POLAR.01.01.002.001",
                            ],
                            "reaction_name": "Example",
                            "reaction_names": ["Example", "Alias example"],
                            "rsmi": "[CH3:1][OH:2]>>[CH3:1][OH:2]",
                            "epd": [["LP-/Sigma+", [1], [1, 2]]],
                            "epd_representation": {
                                "mode": "closed_shell_surrogate",
                                "lwg_formal_charge_overrides": {"1": 0},
                            },
                            "relations": [{"type": "component_of", "target_id": 2}],
                        },
                        {
                            "id": 2,
                            "family": "polar",
                            "tax_code": "POLAR.01.01.001",
                            "tax_codes": ["POLAR.01.01.001"],
                            "entry_code": "POLAR.01.01.001.002",
                            "entry_codes": ["POLAR.01.01.001.002"],
                            "reaction_name": "Target",
                            "reaction_names": ["Target"],
                            "rsmi": "[CH3:1][NH2:2]>>[CH3:1][NH2:2]",
                            "epd": [["LP-/Sigma+", [1], [1, 2]]],
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        hierarchy_path.write_text(
            json.dumps(
                {
                    "taxons": [
                        {
                            "code": "POLAR",
                            "parent_code": None,
                            "level": 1,
                            "name": "Polar",
                        },
                        {
                            "code": "POLAR.01",
                            "parent_code": "POLAR",
                            "level": 2,
                            "name": "Class",
                        },
                        {
                            "code": "POLAR.01.01",
                            "parent_code": "POLAR.01",
                            "level": 3,
                            "name": "Subclass",
                        },
                        {
                            "code": "POLAR.01.01.001",
                            "parent_code": "POLAR.01.01",
                            "level": 4,
                            "name": "Example",
                        },
                        {
                            "code": "POLAR.01.01.002",
                            "parent_code": "POLAR.01.01",
                            "level": 4,
                            "name": "Alias example",
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        (tmp / "rxno_crosswalk.tsv").write_text(
            "\t".join(
                (
                    "taxon_code",
                    "taxon_level",
                    "taxon_name",
                    "relation",
                    "match_type",
                    "score",
                    "ontology_id",
                    "ontology_name",
                )
            )
            + "\n"
            + "\t".join(
                (
                    "POLAR.99.99.999",
                    "4",
                    "Retired example",
                    "skos:exactMatch",
                    "curated",
                    "1.0",
                    "RXNO:0000001",
                    "Example reaction",
                )
            )
            + "\n",
            encoding="utf-8",
        )

        report = build_release_database(
            json_path=json_path,
            hierarchy_path=hierarchy_path,
            db_path=db_path,
        )
        assert report.input_count == 2
        assert report.admitted_count == 2
        assert report.excluded_count == 0
        assert not report.enriched

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM taxon;")
        assert cursor.fetchone()[0] == 5

        cursor.execute("SELECT COUNT(*) FROM reaction;")
        assert cursor.fetchone()[0] == 2

        # External ontology linkage and curated aliases/relations are not
        # duplicated into the core chemistry database.
        cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        tables = {row[0] for row in cursor.fetchall()}
        assert {
            "ontology_release",
            "taxon_xref",
            "reaction_alias",
            "reaction_relation",
        }.isdisjoint(tables)

        cursor.execute(
            "SELECT case_id, name FROM reaction WHERE case_id = 'polar_000001';"
        )
        row = cursor.fetchone()
        assert row is not None
        assert row[1] == "Example"

        cursor.execute(
            "SELECT taxon_code FROM reaction_taxonomy "
            "WHERE reaction_id = 1 ORDER BY taxon_code;"
        )
        assert [r[0] for r in cursor.fetchall()] == [
            "POLAR.01.01.001",
            "POLAR.01.01.002",
        ]

        cursor.execute(
            "SELECT representation_mode, representation_json FROM epd "
            "WHERE reaction_id = 1;"
        )
        mode, representation_json = cursor.fetchone()
        assert mode == "closed_shell_surrogate"
        assert json.loads(representation_json) == {
            "mode": "closed_shell_surrogate",
            "lwg_formal_charge_overrides": {"1": 0},
            "atom_map_namespace": "curated_aam_key",
        }

        cursor.execute(
            "SELECT taxon_code, entry_code, code_index, is_primary "
            "FROM reaction_entry_code "
            "WHERE reaction_id = 1 ORDER BY code_index"
        )
        assert cursor.fetchall() == [
            ("POLAR.01.01.001", "POLAR.01.01.001.001", 0, 1),
            ("POLAR.01.01.002", "POLAR.01.01.002.001", 1, 0),
        ]
        cursor.execute("SELECT DISTINCT graph_format FROM reaction_center;")
        assert cursor.fetchall() == [("synepd.node-link-json.zlib.v1",)]
        cursor.execute("SELECT DISTINCT graph_format FROM its;")
        assert cursor.fetchall() == [("synepd.node-link-json.zlib.v1",)]

        cursor.execute(
            "SELECT construction_version, graph_format, context_hash, "
            "events_json, diagnostics_json FROM mechanism_context "
            "WHERE reaction_id = 1;"
        )
        construction_version, graph_format, context_hash, events, diagnostics = (
            cursor.fetchone()
        )
        assert construction_version == "synepd.mechanistic-center.v1"
        assert graph_format == "synepd.node-link-json.zlib.v1"
        assert len(context_hash) == 64
        assert json.loads(events) == []
        assert json.loads(diagnostics)["epd_atom_maps"] == [1, 2]

        cursor.execute("SELECT COUNT(*) FROM its WHERE mc_id IS NOT NULL")
        assert cursor.fetchone()[0] == 2
        cursor.execute(
            "SELECT transition_edge_count, rc_extension_edge_count, "
            "transient_only_edge_count FROM mechanistic_center ORDER BY id"
        )
        mc_counts = cursor.fetchall()
        assert 1 <= len(mc_counts) <= 2
        assert all(
            transition >= extension >= 0 and transition >= transient >= 0
            for transition, extension, transient in mc_counts
        )

        conn.close()

        original_database = db_path.read_bytes()

        class FailingEditor:
            def apply(self, *args, **kwargs):
                return SimpleNamespace(step_reports=(), matches_product=False)

        monkeypatch.setattr(build_mod, "LWGEditor", FailingEditor)
        with pytest.raises(ValueError, match="Curated EPD does not verify"):
            build_release_database(
                json_path=json_path,
                hierarchy_path=hierarchy_path,
                db_path=db_path,
            )
        assert db_path.read_bytes() == original_database


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


def test_reaction_center_isomorphism_uses_chemical_attributes():
    first = nx.Graph()
    first.add_node(
        1, element=("C", "C"), charge=(0, 1), lone_pairs=(0, 0), hcount=(3, 3)
    )
    second = nx.Graph()
    second.add_node(
        9, element=("N", "N"), charge=(0, 1), lone_pairs=(1, 0), hcount=(2, 2)
    )

    assert not reaction_centers_are_isomorphic(first, second)

    second.nodes[9].update(first.nodes[1])
    assert reaction_centers_are_isomorphic(first, second)


def _clean_payload():
    return {
        "schema": "synepd.clean.polar.v2",
        "count": 1,
        "id_start": 1,
        "records": [
            {
                "id": 1,
                "family": "polar",
                "tax_code": "POLAR.01.01.001",
                "tax_codes": ["POLAR.01.01.001"],
                "entry_code": "POLAR.01.01.001.001",
                "entry_codes": ["POLAR.01.01.001.001"],
                "reaction_name": "Example",
                "reaction_names": ["Example"],
                "rsmi": "[CH3:1][OH:2]>>[CH3:1][OH:2]",
                "epd": [["LP-/Sigma+", [1], [1, 2]]],
            }
        ],
    }


def test_clean_v2_rejects_narrative_representation_metadata():
    payload = _clean_payload()
    payload["records"][0]["epd_representation"] = {
        "mode": "closed_shell_surrogate",
        "lwg_formal_charge_overrides": {"1": 0},
        "scope_note": "Local review prose must not enter the release payload.",
    }

    with pytest.raises(ValueError, match="unsupported fields.*scope_note"):
        validate_clean_v2_payload(payload)


def test_clean_v2_rejects_top_level_label_policy_metadata():
    payload = _clean_payload()
    payload["label_policy"] = "Singular label fields are primary."

    with pytest.raises(ValueError, match="unsupported fields.*label_policy"):
        validate_clean_v2_payload(payload)


def test_clean_v2_rejects_unresolved_relation_targets():
    payload = _clean_payload()
    payload["records"][0]["relations"] = [{"type": "component_of", "target_id": 2}]

    with pytest.raises(ValueError, match=r"missing records: \[2\]"):
        validate_clean_v2_payload(payload)


def test_clean_v2_rejects_unsupported_relation_types():
    payload = _clean_payload()
    payload["records"].append(
        {
            **payload["records"][0],
            "id": 2,
            "entry_code": "POLAR.01.01.001.002",
            "entry_codes": ["POLAR.01.01.001.002"],
        }
    )
    payload["count"] = 2
    payload["records"][0]["relations"] = [{"type": "related_to", "target_id": 2}]

    with pytest.raises(ValueError, match="unsupported relation type"):
        validate_clean_v2_payload(payload)


def test_clean_v2_requires_taxon_entry_code_pairing():
    payload = _clean_payload()
    payload["records"][0]["entry_codes"] = [
        "POLAR.01.01.001.001",
        "POLAR.01.01.002.001",
    ]
    with pytest.raises(ValueError, match="must have equal length"):
        validate_clean_v2_payload(payload)

    payload = _clean_payload()
    payload["records"][0]["entry_code"] = "POLAR.02.01.001.001"
    payload["records"][0]["entry_codes"] = ["POLAR.02.01.001.001"]
    with pytest.raises(ValueError, match="does not belong to paired taxon"):
        validate_clean_v2_payload(payload)


def test_strict_clean_v2_rejects_unknown_taxonomy_codes(monkeypatch, tmp_path):
    payload = _clean_payload()
    payload["records"][0]["tax_code"] = "POLAR.99.99.999"
    payload["records"][0]["tax_codes"] = ["POLAR.99.99.999"]
    payload["records"][0]["entry_code"] = "POLAR.99.99.999.001"
    payload["records"][0]["entry_codes"] = ["POLAR.99.99.999.001"]
    json_path = tmp_path / "polar.json"
    hierarchy_path = tmp_path / "hierarchy.json"
    json_path.write_text(json.dumps(payload), encoding="utf-8")
    hierarchy_path.write_text(
        json.dumps(
            {
                "taxons": [
                    {"code": "POLAR", "parent_code": None, "level": 1, "name": "Polar"}
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown taxonomy codes.*POLAR.99.99.999"):
        build_release_database(json_path, hierarchy_path, tmp_path / "release.sqlite")


def test_strict_clean_v2_rejects_any_build_exclusion(monkeypatch, tmp_path):
    class FakeStandardize:
        def fit(self, rsmi):
            return rsmi

    class FakeCanon:
        def __init__(self, *args, **kwargs):
            pass

        def canonicalise(self, rsmi):
            return SimpleNamespace(canonical_rsmi=rsmi)

    monkeypatch.setattr(build_mod, "Standardize", FakeStandardize)
    monkeypatch.setattr(build_mod, "CanonRSMI", FakeCanon)
    monkeypatch.setattr(
        build_mod,
        "check_reaction_balance",
        lambda _rsmi: SimpleNamespace(balanced=False),
    )
    payload = _clean_payload()
    json_path = tmp_path / "polar.json"
    hierarchy_path = tmp_path / "hierarchy.json"
    json_path.write_text(json.dumps(payload), encoding="utf-8")
    hierarchy_path.write_text(
        json.dumps(
            {
                "taxons": [
                    {"code": "POLAR", "parent_code": None, "level": 1, "name": "Polar"},
                    {
                        "code": "POLAR.01.01.001",
                        "parent_code": None,
                        "level": 4,
                        "name": "Example",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Clean release build excluded records"):
        build_release_database(json_path, hierarchy_path, tmp_path / "release.sqlite")
