"""Validate saved-plan seeds and retain search progress across continuation runs."""

from itrtg_web.models import (
    OptimizerInput,
    OptimizerResult,
    RunRecord,
    ScoreHistoryPoint,
    SearchStats,
)


def same_settings(left: OptimizerInput, right: OptimizerInput) -> bool:
    exclude = {"starting_path"}
    return left.model_dump(exclude=exclude) == right.model_dump(exclude=exclude)


def continuation_path(parent: RunRecord, input_data: OptimizerInput) -> list[int]:
    path = parent.result.path
    if not path or any(step < 0 or step > 24 for step in path):
        raise ValueError("The saved run has no valid starting path.")
    if same_settings(parent.input, input_data):
        return path.copy()

    before = parent.input.current_levels
    current = input_data.current_levels
    if any(level < 0 for level in before + current):
        raise ValueError("Continuation requires non-negative building levels.")
    if any(current[index] < before[index] for index in range(24)):
        raise ValueError("Building levels cannot decrease when continuing a saved run.")
    if input_data.duration_seconds > parent.input.duration_seconds:
        raise ValueError("Event time cannot increase when continuing a saved run.")

    levels = before.copy()
    removed = 0
    for step in path:
        if step == 24:
            break
        target = min(10, levels[step] + 1) if step >= 12 else levels[step] + 1
        levels[step] = target
        if current[step] < target:
            break
        removed += 1
    return path[removed:] or [24]


def _sample_history(points: list[ScoreHistoryPoint], limit: int = 600) -> list[ScoreHistoryPoint]:
    if len(points) <= limit:
        return points
    keep = {0, len(points) - 1}
    for index, point in enumerate(points):
        if point.segment_start:
            keep.add(index)
            keep.add(max(0, index - 1))
    available = [index for index in range(len(points)) if index not in keep]
    remaining = limit - len(keep)
    if remaining > 0:
        keep.update(
            available[int((index + 0.5) * len(available) / remaining)]
            for index in range(remaining)
        )
    return [points[index] for index in sorted(keep)]


class ContinuedSearch:
    def __init__(self, parent: RunRecord, initial: OptimizerResult, input_data: OptimizerInput):
        previous = parent.result.search_stats
        self.elapsed = previous.elapsed_seconds if previous else 0
        self.iterations = previous.iterations if previous else 0
        self.initial_score = initial.score
        self.history = [point.model_copy() for point in previous.score_history] if previous else []
        self.last_improvement = self.history[-1].elapsed_seconds if self.history else self.elapsed
        if previous and previous.last_improvement_elapsed_seconds is not None:
            self.last_improvement = previous.last_improvement_elapsed_seconds
        if (not self.history or self.history[-1].score != parent.result.score
                or self.history[-1].elapsed_seconds < self.elapsed):
            self.history.append(ScoreHistoryPoint(
                elapsed_seconds=self.elapsed, score=parent.result.score,
            ))
        self.new_segment = (
            not same_settings(parent.input, input_data) or initial.score != parent.result.score
        )

    def merge(self, result: OptimizerResult) -> OptimizerResult:
        local = result.search_stats or SearchStats(elapsed_seconds=0, iterations=0)
        local_history = local.score_history or [
            ScoreHistoryPoint(elapsed_seconds=0, score=self.initial_score),
        ]
        if not local.score_history and result.score != self.initial_score:
            local_history.append(ScoreHistoryPoint(
                elapsed_seconds=local.elapsed_seconds, score=result.score,
            ))
        history = self.history.copy()
        for index, point in enumerate(local_history):
            if index == 0 and not self.new_segment and point.score == history[-1].score:
                continue
            history.append(point.model_copy(update={
                "elapsed_seconds": self.elapsed + point.elapsed_seconds,
                "segment_start": self.new_segment and index == 0 or point.segment_start,
            }))
        if result.score > self.initial_score:
            improved_at = local.last_improvement_elapsed_seconds
            last_improvement = self.elapsed + (
                improved_at if improved_at is not None else local.elapsed_seconds
            )
        elif self.new_segment:
            last_improvement = self.elapsed + local_history[0].elapsed_seconds
        else:
            last_improvement = self.last_improvement
        stats = local.model_copy(update={
            "elapsed_seconds": self.elapsed + local.elapsed_seconds,
            "elapsed_seconds_offset": self.elapsed,
            "iterations": self.iterations + local.iterations,
            "score_history": _sample_history(history),
            "last_improvement_elapsed_seconds": last_improvement,
        })
        return result.model_copy(update={"search_stats": stats})
