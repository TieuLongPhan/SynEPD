import os
import hashlib
import zipfile
import unittest.mock as mock
from pathlib import Path
import pytest
from synepd.core.data import (
    DEFAULT_DB_FILENAME,
    DEFAULT_ZENODO_RECORD_ID,
    get_cache_dir,
    get_default_db_path,
    get_github_archive_url,
    get_github_release_api_url,
    get_zenodo_api_url,
    get_zenodo_record_id,
    _verify_checksum,
)


def test_get_cache_dir(tmp_path):
    cache_root = tmp_path / "synepd_test_cache"
    with mock.patch.dict(os.environ, {"SYNEPD_CACHE_DIR": str(cache_root)}):
        cache_dir = get_cache_dir()
        assert cache_dir == cache_root
        assert cache_dir.exists()


def test_get_default_db_path(tmp_path):
    cache_root = tmp_path / "synepd_test_cache"
    with mock.patch.dict(os.environ, {"SYNEPD_CACHE_DIR": str(cache_root)}):
        with mock.patch("synepd.core.data.download_database") as mock_download:
            db_path = cache_root / DEFAULT_DB_FILENAME

            resolved_path = get_default_db_path()
            assert resolved_path == db_path
            mock_download.assert_called_once_with(db_path)


def test_get_default_db_path_uses_cached_current_database_name(tmp_path):
    cache_root = tmp_path / "synepd_test_cache"
    db_path = cache_root / DEFAULT_DB_FILENAME
    cache_root.mkdir()
    db_path.write_bytes(b"sqlite-placeholder")

    with mock.patch.dict(os.environ, {"SYNEPD_CACHE_DIR": str(cache_root)}):
        with mock.patch("synepd.core.data.download_database") as mock_download:
            resolved_path = get_default_db_path()

    assert resolved_path == db_path
    mock_download.assert_not_called()


def test_get_default_db_path_uses_versioned_cache(tmp_path):
    cache_root = tmp_path / "synepd_test_cache"
    with mock.patch.dict(os.environ, {"SYNEPD_CACHE_DIR": str(cache_root)}):
        with mock.patch("synepd.core.data.download_database") as mock_download:
            db_path = cache_root / "epdb-v0.1.0.sqlite"

            resolved_path = get_default_db_path(version="0.1.0", source="github")

    assert resolved_path == db_path
    mock_download.assert_called_once_with(db_path, source="github", version="0.1.0")


def test_release_url_helpers():
    assert get_zenodo_record_id("0.1.0") == DEFAULT_ZENODO_RECORD_ID
    assert get_zenodo_api_url("v0.1.0").endswith(
        f"/api/records/{DEFAULT_ZENODO_RECORD_ID}"
    )
    assert get_github_archive_url("0.1.0").endswith("/refs/tags/v0.1.0.zip")
    assert get_github_release_api_url("0.1.0").endswith("/releases/tags/v0.1.0")


def test_download_database_extracts_sqlite_from_archive(tmp_path):
    archive_path = tmp_path / "release.zip"
    dest_path = tmp_path / "epdb.sqlite"
    sqlite_bytes = b"sqlite-placeholder"

    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("SynEPD-v0.1.0/data/epdb.sqlite", sqlite_bytes)

    with mock.patch("synepd.core.data.urllib.request.urlretrieve") as urlretrieve:

        def fake_urlretrieve(url, filename, reporthook=None):
            Path(filename).write_bytes(archive_path.read_bytes())

        urlretrieve.side_effect = fake_urlretrieve

        from synepd.core.data import download_database

        download_database(dest_path, url="https://example.test/release.zip")

    assert dest_path.read_bytes() == sqlite_bytes


def test_download_database_uses_github_release_database_asset(tmp_path):
    dest_path = tmp_path / "epdb.sqlite"
    release = {
        "assets": [
            {
                "name": DEFAULT_DB_FILENAME,
                "browser_download_url": "https://example.test/epdb.sqlite",
            }
        ]
    }

    with mock.patch("synepd.core.data._load_github_release", return_value=release):
        with mock.patch("synepd.core.data._download_url_to_file") as download:
            from synepd.core.data import download_database

            download_database(dest_path, source="github", version="0.1.0")

    download.assert_called_once_with("https://example.test/epdb.sqlite", dest_path)


def test_download_database_auto_falls_back_to_github(tmp_path):
    dest_path = tmp_path / "epdb.sqlite"
    with mock.patch(
        "synepd.core.data._download_zenodo_database", side_effect=OSError("offline")
    ):
        with mock.patch("synepd.core.data._download_github_database") as github:
            from synepd.core.data import download_database

            download_database(dest_path, source="auto", version="0.1.0")

    github.assert_called_once_with(dest_path, version="0.1.0")


def test_verify_checksum_accepts_valid_digest_and_rejects_mismatch(tmp_path):
    path = tmp_path / "artifact.sqlite"
    path.write_bytes(b"trusted release bytes")
    checksum = f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"

    _verify_checksum(path, checksum)

    with pytest.raises(ValueError, match="checksum validation"):
        _verify_checksum(path, "sha256:" + "0" * 64)
