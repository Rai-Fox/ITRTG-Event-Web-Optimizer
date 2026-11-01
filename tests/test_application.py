"""The user-facing API: profiles, simulation and search lifecycle."""

import logging
from threading import Event
from types import SimpleNamespace

import pytest

from itrtg_web import main
from itrtg_web.models import SearchStats


@pytest.fixture
def running_search(client, game_input, monkeypatch):
    engine = main.registry.get("cpp-local-search")
    initial = engine.simulate(game_input, [0, 24])
    best = initial.model_copy(
        update={
            "score": initial.score + 5,
            "search_stats": SearchStats(elapsed_seconds=0.4, iterations=3),
        }
    )
    ready = Event()

    def optimize(input_data, limits, stop_event=None, progress=None):
        progress(initial)
        progress(best)
        ready.set()
        assert stop_event.wait(5), "The test did not stop its search."
        return best.model_copy(update={"stopped": True})

    # Both updates happen before the next disk checkpoint is due.
    monkeypatch.setattr(main, "time", SimpleNamespace(monotonic=lambda: 100.0))
    monkeypatch.setattr(engine, "optimize", optimize)
    response = client.post("/api/runs", json={"input": game_input.model_dump()})
    assert response.status_code == 200
    assert ready.wait(2), "The search did not publish its first result."
    return SimpleNamespace(id=response.json()["id"], best=best)


def test_profile_can_be_updated_without_losing_limits_or_creating_a_duplicate(client, game_input):
    profile = {
        "name": "Evening plan",
        "input": game_input.model_dump(),
        "limits": {"time_limit_seconds": 12},
    }

    created = client.post("/api/profiles", json=profile)
    assert created.status_code == 200
    profile_id = created.json()["id"]
    updated = client.put(
        f"/api/profiles/{profile_id}",
        json={**profile, "name": "Weekend plan", "limits": {"time_limit_seconds": 18}},
    )

    assert updated.status_code == 200
    saved = client.get("/api/profiles").json()
    assert saved["total"] == 1
    assert saved["items"][0]["name"] == "Weekend plan"
    assert saved["items"][0]["limits"]["time_limit_seconds"] == 18
    assert saved["items"][0]["input"] == profile["input"]

    assert client.delete(f"/api/profiles/{profile_id}").status_code == 204
    assert client.get("/api/profiles").json()["items"] == []


def test_planner_can_discover_an_engine_and_simulate_game_visible_resources(client, game_input):
    engine_id = client.get("/api/engines").json()[0]["id"]
    game_input.resource_counts[1] = 1000

    response = client.post(
        f"/api/engines/{engine_id}/simulate",
        json={"input": game_input.model_dump(), "path": [0, 24]},
    )

    assert response.status_code == 200
    plan = response.json()
    assert plan["purchases"][0]["elapsed_seconds"] == 0
    assert plan["final_levels"][0] == 1
    assert plan["final_resources"][:2] == pytest.approx([1200, 4400])


@pytest.mark.parametrize(
    "invalid_fields",
    [
        pytest.param({"current_levels": [0]}, id="incomplete-resource-grid"),
        pytest.param(
            {"busy_times": [{"start_hour": 2, "end_hour": 1}]},
            id="reversed-busy-period",
        ),
    ],
)
def test_invalid_game_state_is_rejected_before_a_search_is_created(
    client, game_input, invalid_fields
):
    invalid = game_input.model_dump() | invalid_fields

    response = client.post("/api/runs", json={"input": invalid})

    assert response.status_code == 422
    assert client.get("/api/runs").json()["items"] == []


def test_growth_requires_unlocked_pets_before_a_search_can_start(client, game_input):
    game_input.current_levels[11] = 1

    response = client.post("/api/runs", json={"input": game_input.model_dump()})

    assert response.status_code == 400
    assert "Growth" in response.json()["detail"]
    assert client.get("/api/runs").json()["items"] == []


def test_finished_search_can_be_opened_in_history_and_deleted(client, game_input, wait_for_search):
    limits = {"time_limit_seconds": 1, "max_iterations": 1, "seed": 7}
    created = client.post("/api/runs", json={"input": game_input.model_dump(), "limits": limits})
    assert created.status_code == 200
    run_id = created.json()["id"]

    wait_for_search(run_id)
    saved = client.get(f"/api/runs/{run_id}").json()

    assert saved["status"] == "completed"
    assert saved["limits"] == limits
    assert saved["result"]["score"] == saved["score"]
    assert saved["result"]["search_stats"]["stop_reason"] == "iteration_limit"
    assert client.get("/api/runs").json()["items"][0]["id"] == run_id
    assert client.delete(f"/api/runs/{run_id}").status_code == 204
    assert client.get(f"/api/runs/{run_id}").status_code == 404


def test_running_search_cannot_be_duplicated_or_deleted(client, running_search, game_input):
    assert client.post("/api/runs", json={"input": game_input.model_dump()}).status_code == 409
    assert client.delete(f"/api/runs/{running_search.id}").status_code == 409


@pytest.mark.parametrize(
    "stop_url",
    [
        pytest.param("/api/runs/{run_id}/stop", id="stop-search"),
        pytest.param("/api/shutdown", id="shutdown-application"),
    ],
)
def test_live_progress_is_fresh_and_the_best_plan_is_saved_on_stop(
    client, running_search, wait_for_search, stop_url
):
    run_id = running_search.id
    detail = client.get(f"/api/runs/{run_id}").json()
    active = client.get("/api/runs/active").json()[0]
    history = client.get("/api/runs").json()["items"][0]
    for record in (detail, active, history):
        assert record["score"] == running_search.best.score
        assert record["result"]["search_stats"]["iterations"] == 3

    assert client.post(stop_url.format(run_id=run_id)).status_code == 200
    wait_for_search(run_id)
    saved = client.get(f"/api/runs/{run_id}").json()

    assert saved["status"] == "stopped"
    assert saved["result"]["path"] == running_search.best.path
    assert saved["score"] == running_search.best.score
    assert client.get("/api/runs/active").json() == []


def test_failed_search_keeps_its_best_checkpoint_and_logs_the_failure(
    client, game_input, monkeypatch, caplog, wait_for_search
):
    engine = main.registry.get("cpp-local-search")
    best = engine.simulate(game_input, [0, 24])

    def fail_after_checkpoint(input_data, limits, stop_event=None, progress=None):
        progress(best)
        raise ValueError("Search evaluation failed")

    monkeypatch.setattr(engine, "optimize", fail_after_checkpoint)
    with caplog.at_level(logging.ERROR, logger=main.__name__):
        run_id = client.post("/api/runs", json={"input": game_input.model_dump()}).json()["id"]
        wait_for_search(run_id)

    saved = client.get(f"/api/runs/{run_id}").json()
    assert saved["status"] == "failed"
    assert saved["result"]["path"] == best.path
    assert saved["score"] == best.score
    assert saved["error"] == "Search evaluation failed"
    assert f"Optimizer run {run_id} failed" in caplog.text
