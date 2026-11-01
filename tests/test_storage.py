"""Saved plans survive updates, restarts and older history records."""

import pytest

from itrtg_web import main, storage
from itrtg_web.models import RunLimits


def test_restart_keeps_results_but_only_interrupts_unfinished_searches(
    isolated_database, engine, game_input
):
    limits = RunLimits(time_limit_seconds=1, max_iterations=1, seed=7)
    result = engine.optimize(game_input, limits)
    saved_runs = {}
    for status in ("queued", "running", "completed"):
        run = storage.create_run(
            None, engine.metadata().id, engine.metadata().version, game_input, limits
        )
        storage.update_run(run.id, status, result=result, score=result.score)
        saved_runs[status] = run.id

    main.startup()

    statuses = {}
    for previous_status, run_id in saved_runs.items():
        restored = storage.get_run(run_id)
        statuses[previous_status] = restored.status
        assert restored.result == result
        assert restored.limits == limits
    assert statuses == {
        "queued": "interrupted", "running": "interrupted", "completed": "completed"
    }
    assert storage.active_runs() == []


@pytest.mark.parametrize(
    "created_at, expected_date",
    [
        pytest.param("2026-01-02 08:15:30", "2026-01-02T08:15:30Z", id="sqlite-utc"),
        pytest.param("2026-01-02T11:15:30+03:00", "2026-01-02T08:15:30Z", id="offset-date"),
        pytest.param("unknown", None, id="legacy-missing-date"),
    ],
)
def test_history_preserves_zero_points_and_the_original_creation_date(
    client, game_input, created_at, expected_date
):
    run = storage.create_run(None, "cpp-local-search", "0.3.0", game_input, RunLimits())
    with storage.connect() as connection:
        connection.execute("UPDATE runs SET created_at = ? WHERE id = ?", (created_at, run.id))
    storage.update_run(run.id, "completed", score=0)

    history = client.get("/api/runs").json()["items"][0]

    assert history["score"] == 0
    assert history["created_at"] == expected_date
    storage.update_run(run.id, "stopped", score=1.25)
    updated = client.get(f"/api/runs/{run.id}").json()
    assert updated["created_at"] == expected_date
    assert updated["score"] == 1.25
