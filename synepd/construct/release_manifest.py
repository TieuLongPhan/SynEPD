"""Generate a deterministic, checksummed SynEPD release manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

COUNT_TABLES = (
    "reaction",
    "reaction_component",
    "reaction_taxonomy",
    "reaction_entry_code",
    "molecule",
    "taxon",
    "reaction_center",
    "its",
    "epd",
    "epd_arrow",
    "mechanism_context",
    "mechanistic_center",
)


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def generate_release_manifest(
    database_path: Path | str,
    *,
    source_paths: tuple[Path | str, ...] = (),
) -> dict[str, Any]:
    """Describe one built artifact using stable semantic and byte digests."""
    database_path = Path(database_path)
    database_uri = f"{database_path.resolve().as_uri()}?mode=ro&immutable=1"
    with sqlite3.connect(database_uri, uri=True) as connection:
        release_row = connection.execute("""
            SELECT version, release_date, license
            FROM dataset_release ORDER BY version DESC LIMIT 1
            """).fetchone()
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in COUNT_TABLES
            if _table_exists(connection, table)
        }
        migrations = (
            [
                {"version": row[0], "applied_at": row[1], "checksum": row[2]}
                for row in connection.execute("""
                SELECT version, applied_at, checksum
                FROM schema_migration ORDER BY applied_at, version
                """).fetchall()
            ]
            if _table_exists(connection, "schema_migration")
            else []
        )
        graph_formats = {
            table: {
                row[0]: row[1]
                for row in connection.execute(
                    f"SELECT graph_format, COUNT(*) FROM {table} GROUP BY graph_format"
                )
            }
            for table in (
                "reaction_center",
                "its",
                "mechanism_context",
                "mechanistic_center",
            )
            if _table_exists(connection, table)
        }
        context_versions = {row[0]: row[1] for row in connection.execute("""
                    SELECT construction_version, COUNT(*)
                    FROM mechanism_context GROUP BY construction_version
                    """)} if _table_exists(connection, "mechanism_context") else {}
        foreign_key_violations = len(
            connection.execute("PRAGMA foreign_key_check").fetchall()
        )
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]

    sources = []
    for source in sorted((Path(path) for path in source_paths), key=lambda p: str(p)):
        sources.append(
            {
                "name": source.name,
                "bytes": source.stat().st_size,
                "sha256": sha256_file(source),
            }
        )
    crosswalk_paths = tuple(
        path
        for path in (
            database_path.parent / "rxno_crosswalk.tsv",
            database_path.parent.parent
            / "docs"
            / "source"
            / "_static"
            / "rxno_crosswalk.ttl",
        )
        if path.is_file()
    )
    crosswalks = [
        {
            "name": path.name,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in crosswalk_paths
    ]
    return {
        "manifest_version": "synepd.release-manifest.v1",
        "dataset_release": {
            "version": release_row[0] if release_row else None,
            "release_date": release_row[1] if release_row else None,
            "license": release_row[2] if release_row else None,
        },
        "database": {
            "name": database_path.name,
            "bytes": database_path.stat().st_size,
            "sha256": sha256_file(database_path),
            "counts": counts,
            "graph_formats": graph_formats,
            "mechanism_context_versions": context_versions,
            "integrity_check": integrity,
            "foreign_key_violations": foreign_key_violations,
        },
        "schema_migrations": migrations,
        "sources": sources,
        "crosswalks": crosswalks,
    }


def write_release_manifest(manifest: dict[str, Any], output_path: Path | str) -> None:
    """Atomically write canonical JSON for signing or publication."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.building")
    temporary.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, output_path)


def verify_release_manifest(
    database_path: Path | str,
    manifest_path: Path | str,
    *,
    source_paths: tuple[Path | str, ...] = (),
) -> list[str]:
    """Return deterministic verification errors for a release artifact.

    When ``source_paths`` is omitted, sources are resolved beside the manifest
    by their recorded basenames. Callers may pass explicit paths when sources
    live elsewhere.
    """
    database_path = Path(database_path)
    manifest_path = Path(manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"manifest could not be read: {exc}"]

    errors: list[str] = []
    database = manifest.get("database", {})
    expected_hash = database.get("sha256")
    actual_hash = sha256_file(database_path)
    if expected_hash != actual_hash:
        errors.append(
            f"database sha256 mismatch: expected {expected_hash}, got {actual_hash}"
        )
    expected_bytes = database.get("bytes")
    actual_bytes = database_path.stat().st_size
    if expected_bytes != actual_bytes:
        errors.append(
            f"database size mismatch: expected {expected_bytes}, got {actual_bytes}"
        )

    current = generate_release_manifest(database_path)
    for field in (
        "counts",
        "graph_formats",
        "mechanism_context_versions",
        "integrity_check",
        "foreign_key_violations",
    ):
        if database.get(field) != current["database"].get(field):
            errors.append(f"database semantic field mismatch: {field}")
    if manifest.get("dataset_release") != current.get("dataset_release"):
        errors.append("dataset release metadata mismatch")
    if manifest.get("crosswalks", []) != current.get("crosswalks", []):
        errors.append("crosswalk artifact mismatch")

    expected_sources = manifest.get("sources", [])
    if not isinstance(expected_sources, list):
        errors.append("manifest sources field is not a list")
        expected_sources = []

    if source_paths:
        resolved_sources = [Path(path) for path in source_paths]
    else:
        resolved_sources = [
            manifest_path.parent / source["name"]
            for source in expected_sources
            if isinstance(source, dict) and isinstance(source.get("name"), str)
        ]

    actual_by_name: dict[str, Path] = {}
    for source in resolved_sources:
        if source.name in actual_by_name:
            errors.append(f"duplicate source basename supplied: {source.name}")
            continue
        actual_by_name[source.name] = source

    expected_names = {
        source.get("name")
        for source in expected_sources
        if isinstance(source, dict) and isinstance(source.get("name"), str)
    }
    for extra_name in sorted(actual_by_name.keys() - expected_names):
        errors.append(f"unexpected source supplied: {extra_name}")

    for expected in expected_sources:
        if not isinstance(expected, dict) or not isinstance(expected.get("name"), str):
            errors.append("manifest contains an invalid source entry")
            continue
        name = expected["name"]
        source_path = actual_by_name.get(name)
        if source_path is None or not source_path.is_file():
            errors.append(f"source missing: {name}")
            continue
        actual_bytes = source_path.stat().st_size
        if expected.get("bytes") != actual_bytes:
            errors.append(
                f"source size mismatch for {name}: "
                f"expected {expected.get('bytes')}, got {actual_bytes}"
            )
        actual_source_hash = sha256_file(source_path)
        if expected.get("sha256") != actual_source_hash:
            errors.append(
                f"source sha256 mismatch for {name}: "
                f"expected {expected.get('sha256')}, got {actual_source_hash}"
            )
    return errors


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        is not None
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--source", type=Path, action="append", default=[])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify", type=Path, metavar="MANIFEST")
    args = parser.parse_args()
    if args.verify:
        errors = verify_release_manifest(
            args.database,
            args.verify,
            source_paths=tuple(args.source),
        )
        if errors:
            for error in errors:
                print(error)
            return 1
        print(f"Verified release manifest: {args.verify}")
        return 0
    manifest = generate_release_manifest(args.database, source_paths=tuple(args.source))
    write_release_manifest(manifest, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
