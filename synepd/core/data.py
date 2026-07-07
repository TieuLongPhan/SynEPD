import json
import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import quote

# Default release and cache paths
DEFAULT_VERSION = "0.1.0"
DEFAULT_GITHUB_REPOSITORY = "TieuLongPhan/SynEPD"
DEFAULT_ZENODO_RECORD_ID = "21235892"
ZENODO_RECORD_IDS = {
    "0.1.0": DEFAULT_ZENODO_RECORD_ID,
    "v0.1.0": DEFAULT_ZENODO_RECORD_ID,
}
DEFAULT_DB_FILENAME = "epdb.sqlite"
DEFAULT_ARCHIVE_DB_MEMBER = f"data/{DEFAULT_DB_FILENAME}"
DEFAULT_ZENODO_URL = f"https://zenodo.org/records/{DEFAULT_ZENODO_RECORD_ID}"
DEFAULT_DOWNLOAD_URL = DEFAULT_ZENODO_URL


def get_cache_dir() -> Path:
    """Return the platform-specific cache directory for synepd."""
    cache_dir = Path(
        os.environ.get("SYNEPD_CACHE_DIR", Path.home() / ".cache" / "synepd")
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def normalize_version(version: str | None = None) -> str:
    """Return a bare semantic version string without a leading ``v``."""
    release_version = version or DEFAULT_VERSION
    return release_version[1:] if release_version.startswith("v") else release_version


def version_tag(version: str | None = None) -> str:
    """Return the Git tag-style version string for a SynEPD release."""
    return f"v{normalize_version(version)}"


def get_versioned_db_filename(version: str | None = None) -> str:
    """Return the cache filename for a release database."""
    return f"epdb-{version_tag(version)}.sqlite"


def get_zenodo_record_id(version: str | None = None) -> str:
    """Return the Zenodo record ID for a known SynEPD version."""
    normalized = normalize_version(version)
    record_id = ZENODO_RECORD_IDS.get(normalized) or ZENODO_RECORD_IDS.get(
        f"v{normalized}"
    )
    if record_id is None:
        known = ", ".join(sorted({normalize_version(v) for v in ZENODO_RECORD_IDS}))
        raise ValueError(
            f"No Zenodo record is configured for SynEPD {normalized}. "
            f"Known versions: {known}"
        )
    return record_id


def get_github_archive_url(version: str | None = None) -> str:
    """Return the GitHub source archive URL for a SynEPD release tag."""
    tag = quote(version_tag(version), safe="")
    return f"https://github.com/{DEFAULT_GITHUB_REPOSITORY}/archive/refs/tags/{tag}.zip"


def get_zenodo_api_url(version: str | None = None, record_id: str | None = None) -> str:
    """Return the Zenodo API URL for a SynEPD release record."""
    release_record_id = record_id or get_zenodo_record_id(version)
    return f"https://zenodo.org/api/records/{release_record_id}"


def get_default_db_path(
    version: str | None = None, source: str = "zenodo", force: bool = False
) -> Path:
    """Return the path to the cached database, downloading it if not present."""
    use_legacy_cache = version is None and source == "zenodo"
    db_filename = (
        DEFAULT_DB_FILENAME if use_legacy_cache else get_versioned_db_filename(version)
    )
    db_path = get_cache_dir() / db_filename
    if not db_path.exists():
        if use_legacy_cache:
            download_database(db_path)
        else:
            download_database(db_path, source=source, version=version)
    elif force:
        download_database(db_path, source=source, version=version)
    return db_path


def _download_url_to_file(url: str, dest_path: Path) -> None:
    """Download a URL to ``dest_path`` using a temporary file."""
    print(f"Destination: {dest_path}")

    def progress_hook(block_num, block_size, total_size):
        if total_size <= 0:
            return
        downloaded = block_num * block_size
        percent = min(100.0, downloaded * 100.0 / total_size)
        # Print progress carriage-return
        print(
            f"\rProgress: {percent:.1f}% ({downloaded / (1024*1024):.2f} MB of {total_size / (1024*1024):.2f} MB)",
            end="",
            flush=True,
        )

    temp_dest = dest_path.with_suffix(".tmp")
    try:
        urllib.request.urlretrieve(url, temp_dest, reporthook=progress_hook)
        temp_dest.replace(dest_path)
        print("\nDownload complete!")
    except Exception:
        if temp_dest.exists():
            temp_dest.unlink()
        raise


def _copy_archive_member(archive_path: Path, dest_path: Path) -> None:
    """Extract the SynEPD SQLite database from a release ZIP archive."""
    temp_dest = dest_path.with_suffix(".tmp")
    with zipfile.ZipFile(archive_path) as archive:
        names = [
            name
            for name in archive.namelist()
            if not name.endswith("/") and "__MACOSX" not in name
        ]
        candidates = [
            name
            for name in names
            if name == DEFAULT_ARCHIVE_DB_MEMBER
            or name.endswith(f"/{DEFAULT_ARCHIVE_DB_MEMBER}")
            or Path(name).name == DEFAULT_DB_FILENAME
        ]
        if not candidates:
            raise RuntimeError(
                f"Could not find {DEFAULT_ARCHIVE_DB_MEMBER} in release archive"
            )

        member = sorted(candidates, key=len)[0]
        with archive.open(member) as src, temp_dest.open("wb") as dst:
            shutil.copyfileobj(src, dst)
    temp_dest.replace(dest_path)
    print(f"Extracted {member} to {dest_path}")


def _download_archive_database(url: str, dest_path: Path) -> None:
    """Download a release ZIP and extract the packaged SQLite database."""
    with tempfile.TemporaryDirectory(prefix="synepd-download-") as tmp_dir:
        archive_path = Path(tmp_dir) / "release.zip"
        _download_url_to_file(url, archive_path)
        _copy_archive_member(archive_path, dest_path)


def _load_zenodo_record(record_id: str) -> dict:
    api_url = f"https://zenodo.org/api/records/{record_id}"
    with urllib.request.urlopen(api_url) as response:
        return json.load(response)


def _download_zenodo_database(
    dest_path: Path, version: str | None = None, record_id: str | None = None
) -> None:
    """Download the database from a Zenodo record or its release archive."""
    release_record_id = record_id or get_zenodo_record_id(version)
    record = _load_zenodo_record(release_record_id)
    files = record.get("files", [])

    direct_file = next(
        (
            file_info
            for file_info in files
            if Path(file_info.get("key", "")).name == DEFAULT_DB_FILENAME
        ),
        None,
    )
    if direct_file is not None:
        url = direct_file["links"]["self"]
        print("Downloading SynEPD SQLite database from Zenodo...")
        print(f"URL: {url}")
        _download_url_to_file(url, dest_path)
        return

    archive_file = next(
        (
            file_info
            for file_info in files
            if file_info.get("key", "").lower().endswith(".zip")
        ),
        None,
    )
    if archive_file is None:
        raise RuntimeError(
            f"Zenodo record {release_record_id} does not contain "
            f"{DEFAULT_DB_FILENAME} or a ZIP release archive"
        )

    url = archive_file["links"]["self"]
    print("Downloading SynEPD release archive from Zenodo...")
    print(f"URL: {url}")
    _download_archive_database(url, dest_path)


def download_database(
    dest_path: Path,
    url: str | None = None,
    source: str = "zenodo",
    version: str | None = None,
    record_id: str | None = None,
) -> None:
    """Download and cache the SynEPD SQLite database.

    ``source`` can be ``"zenodo"`` or ``"github"``. Zenodo downloads use the
    configured release record for ``version`` and extract ``data/epdb.sqlite``
    if the record stores a source ZIP archive.
    """
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        if url is not None:
            print("Downloading SynEPD database from custom URL...")
            print(f"URL: {url}")
            if url.split("?", 1)[0].lower().endswith(".zip"):
                _download_archive_database(url, dest_path)
            else:
                _download_url_to_file(url, dest_path)
            return

        normalized_source = source.lower()
        if normalized_source == "zenodo":
            _download_zenodo_database(dest_path, version=version, record_id=record_id)
            return

        if normalized_source == "github":
            archive_url = get_github_archive_url(version)
            print("Downloading SynEPD release archive from GitHub...")
            print(f"URL: {archive_url}")
            _download_archive_database(archive_url, dest_path)
            return

        raise ValueError('source must be either "zenodo" or "github"')
    except Exception as e:
        raise RuntimeError(
            f"Failed to download SynEPD database: {e}\n"
            "Please ensure you are online or manually download the file "
            f"and place it at {dest_path}"
        ) from e
