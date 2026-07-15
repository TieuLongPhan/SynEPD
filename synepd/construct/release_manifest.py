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
    "molecule",
    "taxon",
    "reaction_center",
    "its",
    "epd",
    "epd_arrow",
    "mechanism_context",
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
    with sqlite3.connect(database_path) as connection:
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
            for table in ("reaction_center", "its", "mechanism_context")
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
    database_path: Path | str, manifest_path: Path | str
) -> list[str]:
    """Return deterministic verification errors for a release artifact."""
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
        errors = verify_release_manifest(args.database, args.verify)
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
