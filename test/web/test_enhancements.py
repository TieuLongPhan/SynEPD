import re
from pathlib import Path

import pytest
import networkx as nx
from fastapi import HTTPException
from pydantic import ValidationError
from synepd.web.server import (
    app,
    get_reaction_detail,
    get_reactions_by_arrow_count,
    get_reactions_by_arrow_type,
    get_reactions_by_signature,
    search_reactions,
    list_reaction_centers,
    list_mechanistic_centers,
    export_reactions_bulk,
    ExportBulkRequest,
    EPDQueryRequest,
    query_epd,
    serialize_graph,
)


def test_taxonomy_static_assets_match_canonical_hierarchy():
    root = Path(__file__).resolve().parents[2]
    hierarchy = (root / "data" / "hierarchy.md").read_text(encoding="utf-8")
    counts = {
        level: len(re.findall(rf"^{'#' * level} POLAR(?:\.\d+)+ — ", hierarchy, re.M))
        for level in (2, 3, 4)
    }
    summary = f"{counts[2]} families · {counts[3]} subclasses · {counts[4]} leaves"

    for relative_path in (
        "synepd/web/static/index.html",
        "synepd/web/static/taxonomy.html",
        "synepd/web/static/taxonomy_overview.svg",
    ):
        assert summary in (root / relative_path).read_text(encoding="utf-8")


def test_web_surfaces_current_release_links_and_schema():
    root = Path(__file__).resolve().parents[2]
    index = (root / "synepd/web/static/index.html").read_text(encoding="utf-8")
    app_js = (root / "synepd/web/static/app.js").read_text(encoding="utf-8")

    assert "1 887 reactions" not in index
    assert "1,926 reactions" in index
    assert "/static/data_arch.svg" in index
    assert "/static/data_arch.png" in index
    for table_name in ("reaction_component", "reaction_taxonomy"):
        assert table_name in index
    for internal_name in (
        "ontology_release",
        "taxon_xref",
        "reaction_alias",
        "reaction_relation",
        "mechanism_context",
    ):
        assert internal_name not in index

    assert "subtree_reaction_count" in app_js
    assert "ontology_xrefs" in app_js
    assert "reaction_relations" not in app_js
    assert "buildOntologyXrefList" in app_js


def test_direct_and_mechanistic_centers_remain_distinct():
    reaction_centers = list_reaction_centers(limit=2)
    mechanistic_centers = list_mechanistic_centers(limit=2)

    assert reaction_centers["total"] == 1521
    assert mechanistic_centers["total"] == 1540
    assert mechanistic_centers["results"]
    assert "transition_edge_count" in mechanistic_centers["results"][0]
    assert "transient_only_edge_count" in mechanistic_centers["results"][0]
    assert "/api/v1/mechanistic-centers" in app.openapi()["paths"]

    root = Path(__file__).resolve().parents[2]
    diagram_source = (root / "synepd/web/static/data_arch.tex").read_text(
        encoding="utf-8"
    )
    assert "\\ttl{RC}" in diagram_source
    assert "\\ttl{MC}" in diagram_source
    assert "\\Rw{INT}{mc\\_id}{\\fk}" in diagram_source
    assert "reaction\\_MC" not in diagram_source
    assert "reaction\\_mechanistic\\_center" not in diagram_source
    assert "wlhash" in diagram_source
    assert "template\\_graph" in diagram_source
    assert (root / "synepd/web/static/data_arch.svg").stat().st_size > 0
    png = (root / "synepd/web/static/data_arch.png").read_bytes()
    assert len(png) > 25
    assert png[25] in {4, 6}  # grayscale-alpha or RGBA; the fallback stays transparent


def test_alkene_bromination_detail_exposes_transient_bromonium_mc_edge():
    matches = search_reactions(query="Alkene bromination")["results"]
    reaction = next(row for row in matches if row["name"] == "Alkene bromination")
    detail = get_reaction_detail(reaction["id"])

    mc = detail["mechanistic_center"]
    assert mc["epd_enriched_vs_rc"]
    assert mc["structurally_extends_rc"]
    assert mc["transient_only_edge_count"] >= 1
    bromonium = next(
        link
        for link in mc["template_graph"]["links"]
        if {link["source"], link["target"]} == {2, 3}
    )
    assert "transition" in bromonium["mechanistic_roles"]
    assert "transient_only" in bromonium["mechanistic_roles"]


def test_explorer_autoplays_at_4x_and_uses_cdk_with_rdkit_fallback():
    root = Path(__file__).resolve().parents[2]
    index = (root / "synepd/web/static/index.html").read_text(encoding="utf-8")
    app_js = (root / "synepd/web/static/app.js").read_text(encoding="utf-8")
    style = (root / "synepd/web/static/style.css").read_text(encoding="utf-8")

    assert 'value="500" selected' in index
    assert 'onchange="changePlaybackSpeed()"' in index
    assert "renderReactionDepict();\n    startPlayback();" in app_js
    assert "window.SYNEPD_CDK_DEPICT_BASE" in app_js
    assert "img.dataset.renderer = 'cdk'" in app_js
    assert "img.dataset.renderer = 'rdkit'" in app_js
    assert "CDK and RDKit depictions unavailable" in app_js
    assert ".depict-renderer-badge.fallback" in style


def test_frontend_p0_regressions_are_fixed_and_access_preferences_persist():
    root = Path(__file__).resolve().parents[2]
    index = (root / "synepd/web/static/index.html").read_text(encoding="utf-8")
    app_js = (root / "synepd/web/static/app.js").read_text(encoding="utf-8")
    graph_js = (root / "synepd/web/static/graph.js").read_text(encoding="utf-8")
    style = (root / "synepd/web/static/style.css").read_text(encoding="utf-8")

    assert "function copyText(elementId, btn)" in app_js
    assert "const btn = event.currentTarget" not in app_js
    assert "copyText('detail-smiles', this)" in app_js
    assert "url(#${newId})" in app_js
    assert "historyMode: 'none'" in app_js
    assert "historyMode: 'replace'" in app_js
    assert ":root { ${resolvedVariables}; }" in graph_js

    assert 'data-tab="search"' in index
    assert 'aria-labelledby="tabbtn-search"' in index
    assert 'aria-labelledby="tab-search"' not in index
    assert "prefers-reduced-motion: reduce" in style
    assert "synepd_theme" in app_js
    assert "synepd_autoplay" in app_js
    assert "synepd_depict_renderer" in app_js
    assert "CDK Depict is an external service" in app_js


def test_dashboard_exposes_resilient_mechanistic_insights():
    root = Path(__file__).resolve().parents[2]
    index = (root / "synepd/web/static/index.html").read_text(encoding="utf-8")
    app_js = (root / "synepd/web/static/app.js").read_text(encoding="utf-8")
    style = (root / "synepd/web/static/style.css").read_text(encoding="utf-8")

    for element_id in (
        "dash-molecules-val",
        "arrow-type-chart",
        "mc-comparison-chart",
        "rc-reuse-chart",
        "chart-modal-table-toggle",
    ):
        assert f'id="{element_id}"' in index
    assert "summary-ratio-chart" not in index
    assert "Database Object Counts" not in index

    for implementation in (
        "renderArrowTypeMatrix",
        "renderMechanisticCenterComparison",
        "renderStatsError",
        "renderInlineError",
        "weightedQuantile",
        "toggleInsightTable",
        "downloadInsightCSV",
        "downloadInsightSVG",
        "downloadInsightPNG",
        "showInsightReactions",
    ):
        assert implementation in app_js
    assert "function renderDonutChart" not in app_js
    assert "Retry" in app_js
    assert "'arrow-type'," in app_js
    assert "filterKind: 'arrow-count'" in app_js
    assert "filterKind: 'rc-reuse'" in app_js
    assert "top-taxa-chart" not in index
    assert "taxonomy-level-chart" not in index
    assert "renderTaxonomyLadder" not in app_js

    for token in ("--series-8", "--sequential-7", ".insight-table", ".insight-error"):
        assert token in style


def test_acid_chloride_alcoholysis_query_matches_balanced_database_reaction():
    result = query_epd(EPDQueryRequest(rsmi="CC(=O)Cl.OC>>CC(=O)OC"))

    assert result["success"] is True
    assert result["path"] == 1
    assert result["reaction_id"] == 1299
    assert result["case_id"] == "polar_001310"


def test_welcome_panel_keeps_raw_relation_vocabulary_internal():
    root = Path(__file__).resolve().parents[2]
    index = (root / "synepd/web/static/index.html").read_text(encoding="utf-8")

    assert "Database relation figure" in index
    assert re.search(r"app\.js\?v=\d{8}-\d+", index)
    assert re.search(r"data_arch\.svg\?v=\d{8}-\d+", index)
    assert 'onclick="openSchemaModal()"' in index
    assert 'id="schema-modal"' in index
    assert "function closeSchemaModal()" in (
        root / "synepd/web/static/app.js"
    ).read_text(encoding="utf-8")
    assert "resolveMechanisticCenterCount" in (
        root / "synepd/web/static/app.js"
    ).read_text(encoding="utf-8")
    assert "Named-reaction relation table" not in index
    assert "data-relation-type" not in index
    assert "reaction_relation</span>" not in index
    assert "insight-kpi-grid" not in index
    assert "dash-mc-enriched-val" not in index


def test_web_app_enables_response_compression():
    assert any(
        middleware.cls.__name__ == "GZipMiddleware"
        for middleware in app.user_middleware
    )


def test_production_launcher_exposes_worker_and_backlog_controls():
    root = Path(__file__).resolve().parents[2]
    launcher = (root / "run_server.sh").read_text(encoding="utf-8")

    assert 'WORKERS="${SYNEPD_WORKERS:-4}"' in launcher
    assert 'BACKLOG="${SYNEPD_BACKLOG:-2048}"' in launcher
    assert "python -m synepd.construct.build_release_db" in launcher
    assert 'uvicorn_args+=(--workers "$WORKERS")' in launcher


def test_new_reaction_fields():
    search_res = search_reactions(query="POLAR")
    if search_res["results"]:
        rxn_id = search_res["results"][0]["id"]
        detail = get_reaction_detail(rxn_id)
        assert "balanced" in detail
        assert "reactant_atom_count" in detail
        assert "product_atom_count" in detail
        assert "formal_charge_delta" in detail
        assert "rdkit_coords" in detail
        assert "epd_representation" in detail
        assert "canonical_aam_key" in detail
        assert detail["mechanistic_center"]
        assert "mechanism_context" not in detail


def test_mechanistic_roles_survive_web_serialization():
    graph = nx.Graph()
    graph.add_node(1, element="C", atom_map=1, mechanistic_roles=["epd_context"])
    graph.add_node(2, element="O", atom_map=2)
    graph.add_edge(
        1,
        2,
        order=(1.0, 1.0),
        mechanistic_roles=["transition"],
    )

    payload = serialize_graph(graph)

    assert payload["nodes"][0]["mechanistic_roles"] == ["epd_context"]
    assert payload["links"][0]["mechanistic_roles"] == ["transition"]


def test_v1_api_aliases_cover_core_and_knowledge_graph_routes():
    paths = set(app.openapi()["paths"])
    assert "/api/v1/health" in paths
    assert "/api/v1/reactions/{reaction_id}" in paths
    assert "/api/v1/kg/search" in paths
    assert "/api/v1/kg/reactions-by-context-hash" not in paths

    ordered_paths = [
        route.path
        for route in app.routes
        if hasattr(route, "path") and route.path.startswith("/api/v1/reactions/")
    ]
    assert ordered_paths.index("/api/v1/reactions/export-bulk") < ordered_paths.index(
        "/api/v1/reactions/{reaction_id}"
    )


def test_by_arrow_count():
    data = get_reactions_by_arrow_count(n=3, limit=5)
    assert "total" in data
    assert "results" in data
    if data["results"]:
        assert "case_id" in data["results"][0]


def test_by_arrow_type():
    data = get_reactions_by_arrow_type(code="LP-/Sigma+", mode="contains", limit=5)
    assert "total" in data
    assert "results" in data
    if data["results"]:
        assert "case_id" in data["results"][0]


def test_by_signature():
    data = get_reactions_by_signature(
        pattern="LP-/Sigma+", match_type="subsequence", limit=5
    )
    assert "total" in data
    assert "results" in data
    if data["results"]:
        assert "case_id" in data["results"][0]


def test_reaction_centers_smarts():
    data = list_reaction_centers(limit=5)
    assert "results" in data
    if data["results"]:
        assert "smarts" in data["results"][0]


def test_export_reactions_bulk():
    search_res = search_reactions(query="POLAR")
    if search_res["results"]:
        rxn_id = search_res["results"][0]["id"]
        req = ExportBulkRequest(reaction_ids=[rxn_id], template_ids=[])
        response = export_reactions_bulk(req)
        assert response.status_code == 200
        import json

        data = json.loads(response.body.decode("utf-8"))
        assert len(data) == 1
        assert data[0]["id"] == rxn_id
        assert "atom_mapped_smiles" in data[0]


def test_export_reactions_bulk_by_template():
    centers = list_reaction_centers(limit=1)
    assert centers["results"]

    template_id = centers["results"][0]["id"]
    req = ExportBulkRequest(reaction_ids=[], template_ids=[template_id])
    response = export_reactions_bulk(req)

    assert response.status_code == 200
    import json

    data = json.loads(response.body.decode("utf-8"))
    assert data
    assert all("atom_mapped_smiles" in item for item in data)


def test_export_reactions_bulk_rejects_oversized_requests():
    with pytest.raises(ValidationError):
        ExportBulkRequest(reaction_ids=list(range(1, 502)))


def test_export_reactions_bulk_reports_missing_ids():
    req = ExportBulkRequest(reaction_ids=[999_999_999])
    with pytest.raises(HTTPException) as exc_info:
        export_reactions_bulk(req)
    assert exc_info.value.status_code == 404
    assert "999999999" in exc_info.value.detail


def test_export_reactions_bulk_reports_missing_templates():
    req = ExportBulkRequest(template_ids=[999_999_999])
    with pytest.raises(HTTPException) as exc_info:
        export_reactions_bulk(req)
    assert exc_info.value.status_code == 404
    assert "999999999" in exc_info.value.detail
