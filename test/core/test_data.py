import os
import zipfile
import unittest.mock as mock
from pathlib import Path
from synepd.core.data import (
    DEFAULT_DB_FILENAME,
    DEFAULT_ZENODO_RECORD_ID,
    get_cache_dir,
    get_default_db_path,
    get_github_archive_url,
    get_zenodo_api_url,
    get_zenodo_record_id,
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
