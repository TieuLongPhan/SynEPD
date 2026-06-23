import os
import unittest.mock as mock
from pathlib import Path
from synepd.core.data import DEFAULT_DB_FILENAME, get_cache_dir, get_default_db_path


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
