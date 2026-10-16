"""A furniture-only mount must preserve the accounting application's paths."""
from pathlib import Path
import runpy

import pytest


@pytest.mark.parametrize("mount,dedicated,use_volume", [
    ("", "", False),
    ("volume", "", True),
    ("volume", "volume", False),
    ("volume", "different-volume", True),
])
def test_accounting_paths_with_optional_furniture_volume(tmp_path, monkeypatch,
                                                        mount, dedicated, use_volume):
    # Execute a copy in a new empty directory: never import live configuration,
    # load a real dotenv, or touch the user's catalogs/database/storage.
    source = Path(__file__).resolve().parents[2] / "config.py"
    copied = tmp_path / "config.py"
    copied.write_text(source.read_text())
    mount_path = str(tmp_path / mount) if mount else ""
    dedicated_path = str(tmp_path / dedicated) if dedicated else ""
    if mount_path:
        Path(mount_path).mkdir()
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", mount_path)
    monkeypatch.setenv("FURNITURE_VOLUME_MOUNT_PATH", dedicated_path)
    configured = runpy.run_path(str(copied))
    expected = Path(mount_path) if use_volume else tmp_path / "data"
    assert configured["DATABASE_PATH"] == expected / "users.db"
    assert configured["CATEGORY_ALIASES_CSV"] == expected / "alias_category_mapping.csv"
    assert configured["ACCOUNTS_CSV"] == expected / "poster_accounts.csv"
    assert configured["STORAGE_DIR"] == (expected if use_volume else tmp_path) / "storage"
    assert configured["LOGS_DIR"] == (expected if use_volume else tmp_path) / "logs"
    assert not list(tmp_path.rglob("users.db"))
