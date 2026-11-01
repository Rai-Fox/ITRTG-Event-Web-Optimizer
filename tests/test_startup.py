"""A fresh checkout can start without touching the user's existing data."""

import subprocess
import sys
from pathlib import Path
from shutil import copytree, ignore_patterns


def test_import_is_read_only_and_startup_creates_an_empty_database(tmp_path):
    project = Path(__file__).resolve().parents[1]
    backend = copytree(
        project / "backend",
        tmp_path / "checkout" / "backend",
        ignore=ignore_patterns("__pycache__"),
    )
    check_startup = """
import sys

sys.path.insert(0, sys.argv[1])
from itrtg_web import main, storage

assert not storage.DB_PATH.exists(), 'Import wrote to the database'
main.startup()
assert storage.DB_PATH.is_file(), 'Startup did not create the database'
assert storage.list_profiles()[1] == 0
assert storage.list_runs()[1] == 0
"""

    subprocess.run(
        [sys.executable, "-c", check_startup, str(backend)],
        check=True,
        cwd=tmp_path,
        timeout=10,
    )
