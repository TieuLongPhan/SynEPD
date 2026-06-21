import os
import unittest.mock as mock
from pathlib import Path
from synepd.core.data import get_cache_dir, get_default_db_path


def test_get_cache_dir():
    with mock.patch.dict(os.environ, {"SYNEPD_CACHE_DIR": "/tmp/synepd_test_cache"}):
        cache_dir = get_cache_dir()
        assert cache_dir == Path("/tmp/synepd_test_cache")
        assert cache_dir.exists()


def test_get_default_db_path():
    with mock.patch.dict(os.environ, {"SYNEPD_CACHE_DIR": "/tmp/synepd_test_cache"}):
        with mock.patch("synepd.core.data.download_database") as mock_download:
            # If path does not exist, it should trigger download
            db_path = Path("/tmp/synepd_test_cache/release_v1.sqlite")
            if db_path.exists():
                db_path.unlink()

            resolved_path = get_default_db_path()
            assert resolved_path == db_path
            mock_download.assert_called_once_with(db_path)
