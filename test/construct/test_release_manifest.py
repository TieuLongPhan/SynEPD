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
    assert manifest["database"]["integrity_check"] == "ok"
    assert manifest["database"]["foreign_key_violations"] == 0
    assert manifest["sources"][0]["sha256"] == sha256_file(source_path)

    output_path = tmp_path / "manifest.json"
    write_release_manifest(manifest, output_path)
    assert json.loads(output_path.read_text(encoding="utf-8")) == manifest
    assert verify_release_manifest(database_path, output_path) == []

    database_path.write_bytes(database_path.read_bytes() + b"tampered")
    errors = verify_release_manifest(database_path, output_path)
    assert any("sha256 mismatch" in error for error in errors)
