import os
import urllib.request
from pathlib import Path

# Default Zenodo URL and cache paths
# The user will replace this Zenodo Record ID once they upload to Zenodo.
DEFAULT_ZENODO_RECORD_ID = "XXXXXXXX"  # Placeholder for the Zenodo Record ID
DEFAULT_DB_FILENAME = "epdb.sqlite"
DEFAULT_DOWNLOAD_URL = f"https://zenodo.org/records/{DEFAULT_ZENODO_RECORD_ID}/files/{DEFAULT_DB_FILENAME}?download=1"


def get_cache_dir() -> Path:
    """Return the platform-specific cache directory for synepd."""
    cache_dir = Path(
        os.environ.get("SYNEPD_CACHE_DIR", Path.home() / ".cache" / "synepd")
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_default_db_path() -> Path:
    """Return the path to the cached database, downloading it if not present."""
    db_path = get_cache_dir() / DEFAULT_DB_FILENAME
    if not db_path.exists():
        download_database(db_path)
    return db_path


def download_database(dest_path: Path, url: str = DEFAULT_DOWNLOAD_URL) -> None:
    """Download the database file from the Zenodo URL with a progress logger."""
    print("Downloading SynEPD SQLite database from Zenodo...")
    print(f"URL: {url}")
    print(f"Destination: {dest_path}")

    # Simple hook for progress bar
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

    try:
        temp_dest = dest_path.with_suffix(".tmp")
        urllib.request.urlretrieve(url, temp_dest, reporthook=progress_hook)
        temp_dest.replace(dest_path)
        print("\nDownload complete!")
    except Exception as e:
        if temp_dest.exists():
            temp_dest.unlink()
        raise RuntimeError(
            f"Failed to download database from Zenodo: {e}\n"
            "Please ensure you are online or manually download the file "
            f"and place it at {dest_path}"
        ) from e
