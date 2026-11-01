"""Continuing a saved plan retains search effort without reusing a stale score."""

from types import SimpleNamespace

import pytest

from itrtg_web import main, storage
from itrtg_web.continuation import ContinuedSearch
from itrtg_web.models import RunLimits, ScoreHistoryPoint, SearchStats


@pytest.fixture
def saved_plan(client, engine, game_input):
    game_input.resource_counts[1] = 1000
    game_input.current_levels[10] = 1
    game_input.weights["event_currency"] = 1
    result = engine.simulate(game_input, [0, 24])
    result.search_stats = SearchStats(
        elapsed_seconds=10,
        iterations=100,
        score_history=[
            ScoreHistoryPoint(elapsed_seconds=1, score=result.score / 2),
            ScoreHistoryPoint(elapsed_seconds=4, score=result.score),
        ],
        last_improvement_elapsed_seconds=4,
    )
    run = storage.create_run(
        None, result.engine_id, result.engine_version, game_input, RunLimits(),
    )
    return storage.update_run(run.id, "completed", result=result, score=result.score)


def test_continuation_chain_retains_history_and_effort_but_an_independent_search_starts_fresh(
    client, saved_plan, wait_for_search
):
    parent = saved_plan
    limits = {"time_limit_seconds": 10, "max_iterations": 2, "seed": 7}
    for expected_iterations in (102, 104):
        response = client.post("/api/runs", json={
            "parent_run_id": parent.id, "input": saved_plan.input.model_dump(), "limits": limits,
        })
        assert response.status_code == 200
        wait_for_search(response.json()["id"])
        child = storage.get_run(response.json()["id"])
        stats = child.result.search_stats
        assert child.parent_run_id == parent.id
        assert stats.iterations == expected_iterations
        assert stats.elapsed_seconds_offset == parent.result.search_stats.elapsed_seconds
        assert 0 <= stats.elapsed_seconds - stats.elapsed_seconds_offset < 10
        assert stats.stop_reason == "iteration_limit"
        assert stats.score_history[:2] == saved_plan.result.search_stats.score_history
        parent = child

    main.startup()
    restored = client.get(f"/api/runs/{parent.id}").json()
    assert restored["result"] == parent.result.model_dump()
    assert restored["parent_run_id"] == parent.parent_run_id

    fresh = client.post("/api/runs", json={
        "input": saved_plan.input.model_dump(), "limits": limits,
    })
    assert fresh.status_code == 200
    wait_for_search(fresh.json()["id"])
    result = client.get(f"/api/runs/{fresh.json()['id']}").json()
    assert result["parent_run_id"] is None
    assert result["result"]["search_stats"]["iterations"] == 2
    assert result["result"]["search_stats"]["elapsed_seconds_offset"] == 0


def test_same_inputs_keep_the_improvement_timer_but_changed_inputs_start_a_new_score_segment(
    saved_plan, engine
):
    initial = engine.simulate(saved_plan.input, saved_plan.result.path)
    plateau = initial.model_copy(update={"search_stats": SearchStats(
        elapsed_seconds=2, iterations=3,
        score_history=[ScoreHistoryPoint(elapsed_seconds=0.2, score=initial.score)],
        last_improvement_elapsed_seconds=0.2,
    )})
    continued = ContinuedSearch(saved_plan, initial, saved_plan.input).merge(plateau)
    assert continued.search_stats.elapsed_seconds == 12
    assert continued.search_stats.iterations == 103
    assert continued.search_stats.last_improvement_elapsed_seconds == 4
    assert not any(point.segment_start for point in continued.search_stats.score_history)

    changed = saved_plan.input.model_copy(update={"duration_seconds": 1800})
    initial = engine.simulate(changed, saved_plan.result.path)
    assert initial.score < saved_plan.result.score
    improved_score = (initial.score + saved_plan.result.score) / 2
    local = initial.model_copy(update={
        "score": improved_score,
        "search_stats": SearchStats(
            elapsed_seconds=2, iterations=3,
            score_history=[
                ScoreHistoryPoint(elapsed_seconds=0.2, score=initial.score),
                ScoreHistoryPoint(elapsed_seconds=1.5, score=improved_score),
            ],
            last_improvement_elapsed_seconds=1.5,
        ),
    })
    continued = ContinuedSearch(saved_plan, initial, changed).merge(local)
    history = continued.search_stats.score_history
    assert [point.elapsed_seconds for point in history] == [1, 4, 10, 10.2, 11.5]
    assert history[2].score == saved_plan.result.score
    assert history[3].score == initial.score
    assert history[3].segment_start
    assert continued.score == improved_score < saved_plan.result.score
    assert continued.search_stats.last_improvement_elapsed_seconds == 11.5
    assert saved_plan.result.search_stats.score_history[-1].elapsed_seconds == 4


def test_queued_continuation_is_persisted_with_trimmed_seed_and_recalculated_score(
    client, saved_plan, engine, monkeypatch
):
    monkeypatch.setattr(main, "Thread", lambda **kwargs: SimpleNamespace(
        start=lambda: None, join=lambda **kwargs: None, is_alive=lambda: False,
    ))
    changed = saved_plan.input.model_copy(deep=True)
    changed.current_levels[0] = 1
    changed.duration_seconds = 1800
    changed.resource_counts[1] = 50
    expected = engine.simulate(changed, [24])

    response = client.post("/api/runs", json={
        "parent_run_id": saved_plan.id, "input": changed.model_dump(),
    })

    assert response.status_code == 200
    queued = response.json()
    assert queued["status"] == "queued"
    assert queued["input"]["starting_path"] == [24]
    assert queued["score"] == expected.score < saved_plan.result.score
    assert queued["result"]["search_stats"]["iterations"] == 100
    assert queued["result"]["search_stats"]["elapsed_seconds"] == 10
    assert queued["result"]["search_stats"]["score_history"][-1]["segment_start"]
    main.run_latest_results.clear()
    restored = client.get(f"/api/runs/{queued['id']}").json()
    assert restored["result"] == queued["result"]
    main.startup()
    assert client.get(f"/api/runs/{queued['id']}").json()["status"] == "interrupted"


def test_invalid_continuation_does_not_create_a_search(client, saved_plan):
    payload = {"parent_run_id": saved_plan.id, "input": saved_plan.input.model_dump()}
    missing = client.post("/api/runs", json=payload | {"parent_run_id": saved_plan.id + 1})
    assert missing.status_code == 404
    wrong_path = payload["input"] | {"starting_path": [2, 24]}
    assert client.post("/api/runs", json=payload | {"input": wrong_path}).status_code == 400
    more_time = payload["input"] | {"duration_seconds": saved_plan.input.duration_seconds + 1}
    assert client.post("/api/runs", json=payload | {"input": more_time}).status_code == 400
    storage.update_run(saved_plan.id, "failed")
    assert client.post("/api/runs", json=payload).status_code == 409
    storage.update_run(saved_plan.id, "completed")
    with storage.connect() as connection:
        connection.execute(
            "UPDATE runs SET engine_version = 'old-version' WHERE id = ?", (saved_plan.id,),
        )
    assert client.post("/api/runs", json=payload).status_code == 409
    assert client.get("/api/runs").json()["total"] == 1


def test_long_continuation_history_keeps_old_and_new_segment_boundaries(saved_plan, engine):
    old_history = [
        ScoreHistoryPoint(elapsed_seconds=index / 100, score=index) for index in range(1000)
    ]
    saved_plan.result.score = 999
    saved_plan.result.search_stats.score_history = old_history
    saved_plan.result.search_stats.last_improvement_elapsed_seconds = 9.99
    changed = saved_plan.input.model_copy(update={"duration_seconds": 1800})
    initial = engine.simulate(changed, saved_plan.result.path)

    result = ContinuedSearch(saved_plan, initial, changed).merge(initial)

    history = result.search_stats.score_history
    assert len(history) <= 600
    assert history[0] == old_history[0]
    assert history[-2].elapsed_seconds == history[-1].elapsed_seconds == 10
    assert history[-2].score == 999
    assert history[-1].score == initial.score
    assert history[-1].segment_start
