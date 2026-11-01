"""Search limits, warm starts and the progress shown to the user."""

from dataclasses import dataclass
from itertools import count, repeat
from threading import Event
from types import SimpleNamespace

import pytest

from itrtg_web.engines import cpp_local_search
from itrtg_web.models import RunLimits


@dataclass
class SearchClock:
    elapsed_seconds: float = 0

    def advance(self, seconds):
        self.elapsed_seconds += seconds


@pytest.fixture
def search_clock(monkeypatch):
    clock = SearchClock()
    monkeypatch.setattr(
        cpp_local_search,
        "time",
        SimpleNamespace(monotonic=lambda: clock.elapsed_seconds),
    )
    return clock


@pytest.fixture
def scripted_scores(engine, game_input, search_clock, monkeypatch):
    baseline = engine.simulate(game_input, [24])

    def configure(scores, seconds_per_evaluation=0.25):
        scores = iter(scores)

        def evaluate(_input, _path):
            search_clock.advance(seconds_per_evaluation)
            return baseline.model_copy(update={"score": next(scores)})

        monkeypatch.setattr(engine, "simulate", evaluate)

    return configure


@pytest.mark.parametrize(
    "evaluation_seconds, expected_iterations, expected_elapsed",
    [
        pytest.param(0.25, 3, 1, id="time-budget-ends-the-search"),
        pytest.param(1.5, 0, 1.5, id="initial-plan-already-uses-the-budget"),
    ],
)
def test_search_time_budget_includes_the_initial_plan(
    engine, game_input, scripted_scores, evaluation_seconds, expected_iterations, expected_elapsed
):
    scripted_scores(repeat(7), seconds_per_evaluation=evaluation_seconds)

    result = engine.optimize(game_input, RunLimits(time_limit_seconds=1))

    assert result.search_stats.stop_reason == "time_limit"
    assert result.search_stats.iterations == expected_iterations
    assert result.search_stats.elapsed_seconds == pytest.approx(expected_elapsed)
    assert result.score == 7
    assert not result.stopped


def test_iteration_limit_can_finish_before_the_time_budget(
    engine, game_input, scripted_scores
):
    scripted_scores(repeat(7))

    result = engine.optimize(game_input, RunLimits(time_limit_seconds=10, max_iterations=3))

    assert result.search_stats.stop_reason == "iteration_limit"
    assert result.search_stats.iterations == 3
    assert result.search_stats.elapsed_seconds == pytest.approx(1)


def test_user_stop_keeps_the_best_plan_found_before_cancellation(
    engine, game_input, scripted_scores
):
    scripted_scores([7, 8])
    stop = Event()

    def stop_after_improvement(plan):
        if plan.score > 7:
            stop.set()

    result = engine.optimize(
        game_input,
        RunLimits(time_limit_seconds=10),
        stop_event=stop,
        progress=stop_after_improvement,
    )

    assert result.stopped
    assert result.search_stats.stop_reason == "user_stop"
    assert result.search_stats.iterations == 1
    assert result.score == 8


def test_search_starts_from_the_given_path_and_never_returns_a_worse_plan(
    engine, game_input
):
    game_input.starting_path = [0, 12, 24]
    game_input.resource_counts[1] = 5000
    initial_plan = engine.simulate(game_input, game_input.starting_path)
    updates = []

    result = engine.optimize(
        game_input,
        RunLimits(time_limit_seconds=10, max_iterations=5, seed=7),
        progress=updates.append,
    )

    assert updates[0].path == initial_plan.path
    assert updates[0].score == initial_plan.score
    scores = [update.score for update in updates]
    assert scores == sorted(scores)
    assert updates[-1].score == result.score
    assert result.score >= initial_plan.score


def test_only_strict_improvements_change_history_and_improvement_time(
    engine, game_input, scripted_scores
):
    scripted_scores([0, 2, 2, 1, 3, 1], seconds_per_evaluation=0.05)

    result = engine.optimize(game_input, RunLimits(time_limit_seconds=10, max_iterations=5))

    history = result.search_stats.score_history
    assert [point.score for point in history] == [0, 2, 3]
    assert [point.elapsed_seconds for point in history] == pytest.approx([0.05, 0.1, 0.25])
    assert result.search_stats.last_improvement_elapsed_seconds == pytest.approx(0.25)
    assert result.search_stats.elapsed_seconds == pytest.approx(0.3)
    assert result.score == 3


def test_plateau_progress_keeps_the_last_improvement_time(
    engine, game_input, scripted_scores
):
    scripted_scores(repeat(0))
    updates = []

    result = engine.optimize(
        game_input,
        RunLimits(time_limit_seconds=1.25),
        progress=updates.append,
    )

    iterations = [update.search_stats.iterations for update in updates]
    assert any(0 < iteration < result.search_stats.iterations for iteration in iterations)
    assert all(update.score == 0 for update in updates)
    improvement_times = [
        update.search_stats.last_improvement_elapsed_seconds for update in updates
    ]
    assert improvement_times == [0.25] * len(updates)
    assert len(result.search_stats.score_history) == 1
    assert result.search_stats.elapsed_seconds == pytest.approx(1.25)


def test_dense_history_is_bounded_and_earlier_updates_stay_unchanged(
    engine, game_input, scripted_scores
):
    scripted_scores(count(), seconds_per_evaluation=0.001)
    updates = []

    result = engine.optimize(game_input, RunLimits(time_limit_seconds=10), progress=updates.append)

    history = result.search_stats.score_history
    assert len(history) <= 303
    assert history[0].score == 0
    assert history[-1].score == result.score
    assert history[-1].elapsed_seconds == result.search_stats.last_improvement_elapsed_seconds
    assert all(first.score < second.score for first, second in zip(history, history[1:]))
    assert [point.score for point in updates[0].search_stats.score_history] == [0]
    assert all(update.search_stats.score_history[-1].score == update.score for update in updates)
