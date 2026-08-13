import json

from synepd.construct.release_manifest import (
    generate_release_manifest,
    sha256_file,
    verify_release_manifest,
    write_release_manifest,
)
from synepd.database.models import ReleaseDatabase


def test_release_manifest_has_checksums_counts_and_integrity(tmp_path):
    database_path = tmp_path / "release.sqlite"
    source_path = tmp_path / "polar.json"
    source_path.write_text('{"records": []}\n', encoding="utf-8")
    with ReleaseDatabase(database_path) as database:
        database.create_tables()

    manifest = generate_release_manifest(database_path, source_paths=(source_path,))

    assert manifest["manifest_version"] == "synepd.release-manifest.v1"
    assert manifest["database"]["sha256"] == sha256_file(database_path)
    assert manifest["database"]["counts"]["reaction"] == 0
    assert manifest["database"]["counts"]["reaction_component"] == 0
    assert manifest["database"]["counts"]["reaction_taxonomy"] == 0
    assert manifest["database"]["integrity_check"] == "ok"
    assert manifest["database"]["foreign_key_violations"] == 0
    assert manifest["sources"][0]["sha256"] == sha256_file(source_path)

    output_path = tmp_path / "manifest.json"
    write_release_manifest(manifest, output_path)
    assert json.loads(output_path.read_text(encoding="utf-8")) == manifest
    assert verify_release_manifest(database_path, output_path) == []

    source_path.write_text('{"records": [{"id": 1}]}\n', encoding="utf-8")
    source_errors = verify_release_manifest(database_path, output_path)
    assert any("source sha256 mismatch" in error for error in source_errors)

    source_path.unlink()
    missing_errors = verify_release_manifest(database_path, output_path)
    assert "source missing: polar.json" in missing_errors

    database_path.write_bytes(database_path.read_bytes() + b"tampered")
    errors = verify_release_manifest(database_path, output_path)
    assert any("sha256 mismatch" in error for error in errors)


def test_release_manifest_accepts_explicit_source_paths(tmp_path):
    database_path = tmp_path / "release.sqlite"
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    source_path = source_dir / "polar.json"
    source_path.write_text('{"records": []}\n', encoding="utf-8")
    with ReleaseDatabase(database_path) as database:
        database.create_tables()

    manifest = generate_release_manifest(database_path, source_paths=(source_path,))
    manifest_path = tmp_path / "manifest.json"
    write_release_manifest(manifest, manifest_path)

    assert (
        verify_release_manifest(
            database_path,
            manifest_path,
            source_paths=(source_path,),
        )
        == []
    )


def test_release_manifest_checks_crosswalk_artifacts(tmp_path):
    data_dir = tmp_path / "data"
    docs_static_dir = tmp_path / "docs" / "source" / "_static"
    data_dir.mkdir()
    docs_static_dir.mkdir(parents=True)
    database_path = data_dir / "release.sqlite"
    tsv_path = data_dir / "rxno_crosswalk.tsv"
    ttl_path = docs_static_dir / "rxno_crosswalk.ttl"
    tsv_path.write_text("taxon_code\tontology_id\n", encoding="utf-8")
    ttl_path.write_text("# mapping\n", encoding="utf-8")
    with ReleaseDatabase(database_path) as database:
        database.create_tables()

    manifest = generate_release_manifest(database_path)
    assert [item["name"] for item in manifest["crosswalks"]] == [
        "rxno_crosswalk.tsv",
        "rxno_crosswalk.ttl",
    ]
    manifest_path = tmp_path / "manifest.json"
    write_release_manifest(manifest, manifest_path)
    assert verify_release_manifest(database_path, manifest_path) == []

    ttl_path.write_text("# changed mapping\n", encoding="utf-8")
    assert "crosswalk artifact mismatch" in verify_release_manifest(
        database_path, manifest_path
    )
