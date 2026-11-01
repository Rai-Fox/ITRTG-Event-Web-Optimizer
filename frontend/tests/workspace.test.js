import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { inputAtTimeline, readWorkspace, writeWorkspace } from "../src/workspace.js";
import { gameInput, memoryStorage, upgrade } from "./fixtures.js";

describe("Keeping the working state", () => {
  test("restores the draft and selected forecast without restoring an old iteration cap", () => {
    const storage = memoryStorage();
    const workspace = {
      input: gameInput(),
      limits: { time_limit_seconds: 120, max_iterations: 50000, seed: 7 },
      selectedRunId: 18,
      preview: true,
      timelineSeconds: 30,
    };

    writeWorkspace(storage, workspace);
    const restored = readWorkspace(storage, gameInput());

    assert.deepEqual(restored.input, workspace.input);
    assert.deepEqual(restored.limits, {
      time_limit_seconds: 120,
      max_iterations: null,
      seed: 7,
    });
    assert.equal(restored.selectedRunId, 18);
    assert.equal(restored.preview, true);
    assert.equal(restored.timelineSeconds, 30);
  });

  test("starts with an independent default draft when saved data cannot be read", () => {
    const defaults = gameInput();
    const restored = readWorkspace({ getItem: () => "broken json" }, defaults);

    assert.deepEqual(restored.input, defaults);
    restored.input.current_levels[upgrade.graveStone] = 10;
    assert.equal(defaults.current_levels[upgrade.graveStone], 0);
  });

  test("adopting a forecast advances the state and shifts only remaining busy periods", () => {
    const input = gameInput({
      duration_seconds: 24 * 3600,
      starting_path: [upgrade.graveStone, upgrade.finish],
      busy_times: [
        { start_hour: 1, end_hour: 2 },
        { start_hour: 6, end_hour: 10 },
        { start_hour: 10, end_hour: 12 },
      ],
    });
    const before = structuredClone(input);
    const forecast = {
      elapsed_seconds: 8 * 3600,
      levels: Array(25).fill(2),
      resources: Array(12).fill(50),
    };

    const adopted = inputAtTimeline(input, forecast);

    assert.equal(adopted.duration_seconds, 16 * 3600);
    assert.deepEqual(adopted.current_levels, forecast.levels);
    assert.deepEqual(adopted.resource_counts, forecast.resources);
    assert.deepEqual(adopted.busy_times, [
      { start_hour: 0, end_hour: 2 },
      { start_hour: 2, end_hour: 4 },
    ]);
    assert.deepEqual(adopted.starting_path, []);
    assert.deepEqual(input, before);
  });
});
