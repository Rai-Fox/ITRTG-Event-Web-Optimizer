"""Shared domain data and explicit, isolated application resources."""

from collections.abc import Iterator
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from itrtg_web import storage
from itrtg_web.engines.cpp_local_search import CppLocalSearchEngine
from itrtg_web.models import OptimizerInput


@pytest.fixture(autouse=True)
def protect_saved_data(tmp_path, monkeypatch) -> None:
    """Never let a test accidentally connect to the user's saved database."""
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "test.sqlite3")


@pytest.fixture
def isolated_database(protect_saved_data) -> Path:
    storage.init_db()
    return storage.DB_PATH


@pytest.fixture
def client(protect_saved_data, monkeypatch) -> Iterator[TestClient]:
    from itrtg_web import main

    for name in ("run_stop_events", "run_progress", "run_latest_results", "run_threads"):
        monkeypatch.setattr(main, name, {})
    with TestClient(main.app) as api_client:
        yield api_client


@pytest.fixture
def engine() -> CppLocalSearchEngine:
    return CppLocalSearchEngine()


@pytest.fixture
def game_input() -> OptimizerInput:
    return OptimizerInput(duration_seconds=3600)


@pytest.fixture
def wait_for_search():
    from itrtg_web import main

    def wait(run_id: int) -> None:
        worker = main.run_threads.get(run_id)
        if worker is not None:
            worker.join(timeout=5)
            assert not worker.is_alive(), "The search worker did not finish."

    return wait
