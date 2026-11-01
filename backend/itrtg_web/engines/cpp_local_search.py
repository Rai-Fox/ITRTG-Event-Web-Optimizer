from __future__ import annotations

import random
import time
from copy import deepcopy
from threading import Event

from itrtg_web.engines.base import OptimizerEngine, ProgressCallback
from itrtg_web.models import (
    EngineMetadata,
    OptimizerInput,
    OptimizerResult,
    Purchase,
    RESOURCE_KEYS,
    RESOURCE_LABELS,
    RunLimits,
    ScoreHistoryPoint,
    SearchStats,
    TimelineSnapshot,
)

NUM_RESOURCES = 12
NUM_UPGRADES = NUM_RESOURCES * 2
COMPLETE = NUM_UPGRADES
INFINITY_VALUE = 1e100

CYCLE_TIME_MULTIPLIER = [
    1.0 / 3.0,
    1.0,
    1.0 / 3.0,
    1.0 / 5.0,
    1.0 / 5.0,
    1.0 / 5.0,
    1.0 / 2500.0,
    1.0 / 5.0,
    1.0 / 1200.0,
    1.0 / 1200.0,
    1.0 / 5000.0,
    1.0 / 1800.0,
]
SPEED_MULTIPLIERS = [1.25**i for i in range(11)]


def resource_display_multipliers(input_data: OptimizerInput) -> list[float]:
    multipliers = [1.0] * NUM_RESOURCES
    multipliers[6] = (500.0 + input_data.dungeon_levels) / 5.0
    multipliers[9] = 0.5 + input_data.adventure_level / 100.0
    multipliers[11] = input_data.unlocked_pets / 100.0
    return multipliers


def to_internal_resources(input_data: OptimizerInput, resources: list[float]) -> list[float]:
    internal = [float(value) for value in resources]
    for index, multiplier in enumerate(resource_display_multipliers(input_data)):
        if multiplier == 0:
            internal[index] = 0.0
        else:
            internal[index] /= multiplier
    return internal


def to_display_resources(input_data: OptimizerInput, resources: list[float]) -> list[float]:
    return [
        float(value) * multiplier
        for value, multiplier in zip(resources, resource_display_multipliers(input_data), strict=True)
    ]


def upgrade_name(upgrade_type: int) -> str:
    if upgrade_type == COMPLETE:
        return "Complete"
    suffix = "Level" if upgrade_type < NUM_RESOURCES else "Speed"
    return f"{RESOURCE_LABELS[upgrade_type % NUM_RESOURCES]} {suffix}"


def production_rates(levels: list[int]) -> list[float]:
    rates = []
    for index in range(NUM_RESOURCES):
        speed_level = max(0, min(10, levels[index + NUM_RESOURCES]))
        rates.append(levels[index] * CYCLE_TIME_MULTIPLIER[index] * SPEED_MULTIPLIERS[speed_level])
    return rates


def cost_entries(upgrade_type: int, current_level: int) -> list[tuple[int, float]]:
    if upgrade_type == COMPLETE:
        return [(1, INFINITY_VALUE)]
    resource_type = upgrade_type % NUM_RESOURCES
    base_cost = float((current_level + 1) ** 3) * 100.0
    if upgrade_type >= NUM_RESOURCES:
        base_cost *= 2.5
    # Halloween 2026 amounts match Discord; ingredient positions and cycle times
    # are inherited from Summer 2026 and still need confirmation in game.
    dependencies = {
        0: [(1, 2.0)],
        1: [(1, 1.0)],
        2: [(1, 2.0)],
        3: [(0, 1.5), (1, 1.5)],
        4: [(1, 2.0)],
        5: [(1, 1.5), (2, 1.5)],
        6: [(3, 5.0)],
        7: [(1, 1.5), (4, 1.5)],
        8: [(5, 5.0)],
        9: [(3, 2.5), (4, 2.5)],
        10: [(3, 3.0), (4, 3.0), (5, 3.0)],
        11: [(5, 2.5), (7, 2.5)],
    }[resource_type]
    return [(idx, base_cost * multiplier) for idx, multiplier in dependencies]


def busy_delay(input_data: OptimizerInput, expected_elapsed: float) -> float:
    available = expected_elapsed
    for busy in sorted(input_data.busy_times, key=lambda item: item.start_hour):
        start = busy.start_hour * 3600.0
        end = busy.end_hour * 3600.0
        if start <= available < end:
            available = end
    return available - expected_elapsed


class CppLocalSearchEngine(OptimizerEngine):
    def metadata(self) -> EngineMetadata:
        return EngineMetadata(
            id="cpp-local-search",
            name="Halloween 2026 (C++ Local Search Compatible)",
            version="0.3.0",
            capabilities=["simulate", "optimize", "timeline", "timed-runs", "csv-path-export"],
            config_schema=self.schema(),
        )

    def schema(self) -> dict:
        return {
            "runLimits": {
                "timeLimitSeconds": {"type": "number", "default": 30, "minimum": 0.1},
                "maxIterations": {"type": ["integer", "null"], "default": None, "minimum": 1},
                "seed": {"type": ["integer", "null"], "default": None},
            },
            "advancedSettings": {
                "weights": RESOURCE_KEYS,
                "busyTimes": "Array of {start_hour, end_hour}, relative to run start.",
            },
        }

    def validate(self, input_data: OptimizerInput) -> None:
        if len(input_data.current_levels) != 25:
            raise ValueError("current_levels must have 25 values")
        if len(input_data.resource_counts) != NUM_RESOURCES:
            raise ValueError("resource_counts must have 12 values")
        if input_data.unlocked_pets == 0 and (input_data.current_levels[11] > 0 or input_data.resource_counts[11] > 0):
            raise ValueError("Enter your unlocked pet count to calculate Growth")

    def simulate(self, input_data: OptimizerInput, path: list[int]) -> OptimizerResult:
        self.validate(input_data)
        levels = input_data.current_levels.copy()
        resources = to_internal_resources(input_data, input_data.resource_counts)
        remaining = float(input_data.duration_seconds)
        purchases: list[Purchase] = []
        snapshots = [self._snapshot(input_data, levels, resources, remaining)]
        actual_path: list[int] = []

        for upgrade_type in path:
            if remaining <= 1e-3:
                break
            if upgrade_type < 0 or upgrade_type > COMPLETE:
                continue
            if upgrade_type >= NUM_RESOURCES and upgrade_type < COMPLETE and levels[upgrade_type] >= 10:
                continue

            time_taken, purchased = self._perform_upgrade(input_data, levels, resources, upgrade_type, remaining)
            remaining -= time_taken
            actual_path.append(upgrade_type)
            if purchased:
                purchases.append(
                    Purchase(
                        index=len(purchases),
                        upgrade_type=upgrade_type,
                        upgrade_name=upgrade_name(upgrade_type),
                        elapsed_seconds=input_data.duration_seconds - remaining,
                        level_after=levels[upgrade_type],
                    )
                )
            snapshot = self._snapshot(input_data, levels, resources, remaining)
            if snapshot.elapsed_seconds == snapshots[-1].elapsed_seconds:
                snapshots[-1] = snapshot
            else:
                snapshots.append(snapshot)

        if remaining > 1e-3:
            rates = production_rates(levels)
            for index in range(NUM_RESOURCES):
                resources[index] += rates[index] * remaining
            remaining = 0.0
            snapshots.append(self._snapshot(input_data, levels, resources, remaining))

        score = self._score(input_data, resources)
        return OptimizerResult(
            engine_id=self.metadata().id,
            engine_version=self.metadata().version,
            score=score,
            path=actual_path,
            purchases=purchases,
            final_levels=levels,
            final_resources=to_display_resources(input_data, resources),
            snapshots=snapshots,
        )

    def optimize(
        self,
        input_data: OptimizerInput,
        limits: RunLimits,
        stop_event: Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> OptimizerResult:
        started_at = time.monotonic()
        deadline = started_at + limits.time_limit_seconds
        rng = random.Random(limits.seed)
        path = input_data.starting_path.copy() or self._seed_path(rng, max(48, input_data.duration_seconds // 1800))
        best = self.simulate(input_data, path)
        no_improvement = 0
        iterations = 0
        last_progress = time.monotonic()
        last_improvement = max(0, last_progress - started_at)
        score_history = [ScoreHistoryPoint(elapsed_seconds=last_improvement, score=best.score)]
        history_interval = limits.time_limit_seconds / 300
        history_bucket = None

        def search_stats(now: float, reason=None) -> SearchStats:
            return SearchStats(
                elapsed_seconds=max(0, now - started_at),
                iterations=iterations,
                stop_reason=reason,
                score_history=score_history.copy(),
                last_improvement_elapsed_seconds=last_improvement,
            )

        best.search_stats = search_stats(last_progress)
        if progress:
            progress(best.model_copy())

        stop_reason = "iteration_limit"
        while limits.max_iterations is None or iterations < limits.max_iterations:
            if stop_event and stop_event.is_set():
                best.stopped = True
                stop_reason = "user_stop"
                break
            if time.monotonic() >= deadline:
                stop_reason = "time_limit"
                break

            candidate_path = self._mutate_path(rng, best.path or path, iterations)
            candidate = self.simulate(input_data, candidate_path)
            iterations += 1
            now = time.monotonic()
            if candidate.score > best.score:
                best = candidate
                no_improvement = 0
                last_improvement = max(0, now - started_at)
                point = ScoreHistoryPoint(elapsed_seconds=last_improvement, score=best.score)
                bucket = int(last_improvement / history_interval)
                if len(score_history) > 1 and bucket == history_bucket:
                    score_history[-1] = point
                else:
                    score_history.append(point)
                history_bucket = bucket
            else:
                no_improvement += 1
            if no_improvement > 250:
                path = self._seed_path(rng, max(48, input_data.duration_seconds // 1800))
                no_improvement = 0
            else:
                path = best.path
            if progress and now - last_progress >= 0.25:
                best.search_stats = search_stats(now)
                progress(best.model_copy())
                last_progress = now

        if stop_event and stop_event.is_set():
            best.stopped = True
            stop_reason = "user_stop"
        best.search_stats = search_stats(time.monotonic(), stop_reason)
        if progress:
            progress(best.model_copy())
        return best

    def timeline(self, result: OptimizerResult, t_seconds: float) -> TimelineSnapshot:
        if not result.snapshots:
            raise ValueError("Run result has no timeline snapshots")
        t_seconds = max(0.0, min(float(t_seconds), result.snapshots[-1].elapsed_seconds))
        base = result.snapshots[0]
        for snapshot in result.snapshots:
            if snapshot.elapsed_seconds <= t_seconds:
                base = snapshot
            else:
                break
        elapsed_delta = t_seconds - base.elapsed_seconds
        resources = [
            base.resources[index] + base.production_rates[index] * elapsed_delta
            for index in range(NUM_RESOURCES)
        ]
        return TimelineSnapshot(
            elapsed_seconds=t_seconds,
            levels=base.levels.copy(),
            resources=resources,
            production_rates=base.production_rates.copy(),
        )

    def _perform_upgrade(
        self,
        input_data: OptimizerInput,
        levels: list[int],
        resources: list[float],
        upgrade_type: int,
        remaining: float,
    ) -> tuple[float, bool]:
        rates = production_rates(levels)
        needed_time = 0.0
        for resource_idx, amount in cost_entries(upgrade_type, levels[upgrade_type]):
            missing = amount - resources[resource_idx]
            if missing <= 0:
                continue
            if rates[resource_idx] == 0:
                needed_time = remaining
                break
            needed_time = max(needed_time, missing / rates[resource_idx])

        elapsed = input_data.duration_seconds - remaining
        needed_time = min(needed_time, remaining)
        needed_time = min(remaining, needed_time + busy_delay(input_data, elapsed + needed_time))
        for index in range(NUM_RESOURCES):
            resources[index] += rates[index] * needed_time

        if needed_time < remaining and upgrade_type != COMPLETE:
            for resource_idx, amount in cost_entries(upgrade_type, levels[upgrade_type]):
                resources[resource_idx] -= amount
            levels[upgrade_type] += 1
            return needed_time, True
        return needed_time, False

    def _snapshot(
        self,
        input_data: OptimizerInput,
        levels: list[int],
        resources: list[float],
        remaining: float,
    ) -> TimelineSnapshot:
        return TimelineSnapshot(
            elapsed_seconds=float(input_data.duration_seconds) - remaining,
            levels=levels.copy(),
            resources=to_display_resources(input_data, resources),
            production_rates=to_display_resources(input_data, production_rates(levels)),
        )

    def _score(self, input_data: OptimizerInput, resources: list[float]) -> float:
        score = sum(value * 1e-15 for value in resources)
        event_currency = resources[10]
        weights = input_data.weights
        score += (min(event_currency, 10000.0) + max(0.0, event_currency - 10000.0) * 0.005) * weights.get("event_currency", 100e-5)
        score += resources[6] * weights.get("free_exp", 6e-5)
        score += resources[8] * weights.get("pet_stones", 3.5e-5)
        score += resources[9] * weights.get("research_points", 2e-5)
        score += resources[11] * weights.get("growth", 8e-5)
        return score

    def _random_path(self, rng: random.Random, length: int) -> list[int]:
        path = [rng.randrange(NUM_UPGRADES) for _ in range(min(int(length), 400))]
        path.append(COMPLETE)
        return path

    def _seed_path(self, rng: random.Random, length: int) -> list[int]:
        backbone = [
            1, 1, 1, 1,
            0, 2, 4, 3, 5, 7,
            12, 13, 14, 16, 15, 17, 19,
            6, 8, 9, 10, 11,
            18, 20, 21, 22, 23,
        ]
        path: list[int] = []
        while len(path) < min(int(length), 500):
            path.extend(backbone)
        path = path[: min(int(length), 500)]
        path.append(COMPLETE)
        return path

    def _mutate_path(self, rng: random.Random, path: list[int], iteration: int) -> list[int]:
        candidate = deepcopy(path)
        if not candidate:
            return self._random_path(rng, 24)
        if candidate[-1] != COMPLETE:
            candidate.append(COMPLETE)
        body_len = max(1, len(candidate) - 1)
        strategy = iteration % 100
        if strategy < 20:
            candidate.insert(rng.randrange(body_len + 1), rng.randrange(NUM_UPGRADES))
        elif strategy < 40 and body_len > 1:
            del candidate[rng.randrange(body_len)]
        elif strategy < 65:
            candidate[rng.randrange(body_len)] = rng.randrange(NUM_UPGRADES)
        elif strategy < 90 and body_len > 1:
            a, b = rng.sample(range(body_len), 2)
            candidate[a], candidate[b] = candidate[b], candidate[a]
        elif body_len > 3:
            start = rng.randrange(0, body_len - 2)
            end = rng.randrange(start + 2, body_len)
            item = candidate.pop(start)
            candidate.insert(end, item)
        return candidate
