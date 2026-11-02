import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  inputAtTimeline,
  readChecklist,
  readWorkspace,
  reconcileWorkspace,
  writeWorkspace,
  writeChecklist,
} from "../src/workspace.js";
import { gameInput, memoryStorage, upgrade } from "./fixtures.js";

describe("Keeping the working state", () => {
  test("keeps checked purchases for each saved plan and database independently", () => {
    const storage = memoryStorage();
    writeChecklist(storage, "windows", 18, [0, 3]);
    writeChecklist(storage, "windows", 19, [1]);
    writeChecklist(storage, "docker", 18, [2]);

    assert.deepEqual(readChecklist(storage, "windows", 18), [0, 3]);
    assert.deepEqual(readChecklist(storage, "windows", 19), [1]);
    assert.deepEqual(readChecklist(storage, "docker", 18), [2]);
    writeChecklist(storage, "windows", 18, [3]);
    assert.deepEqual(readChecklist(storage, "windows", 18), [3]);
    assert.deepEqual(readChecklist(storage, "windows", 20), []);
  });

  test("restores the draft and forecast in the same database without an old iteration cap", () => {
    const storage = memoryStorage();
    const workspace = {
      input: gameInput(),
      limits: { time_limit_seconds: 120, max_iterations: 50000, seed: 7 },
      instanceId: "windows-database",
      profileId: 3,
      selectedRunId: 18,
      preview: true,
      timelineSeconds: 30,
    };

    writeWorkspace(storage, workspace);
    const restored = reconcileWorkspace(
      readWorkspace(storage, gameInput()),
      "windows-database",
    );

    assert.deepEqual(restored.input, workspace.input);
    assert.deepEqual(restored.limits, {
      time_limit_seconds: 120,
      max_iterations: null,
      seed: 7,
    });
    assert.equal(restored.instanceId, "windows-database");
    assert.equal(restored.profileId, 3);
    assert.equal(restored.selectedRunId, 18);
    assert.equal(restored.preview, true);
    assert.equal(restored.timelineSeconds, 30);
  });

  test("drops database references from another or unidentified database while keeping the draft", () => {
    const examples = [
      {
        name: "another database's profile",
        instanceId: "windows-database",
        sourceInfo: { kind: "profile", name: "Evening plan" },
      },
      {
        name: "legacy saved result without database identity",
        sourceInfo: { kind: "run", id: 18 },
      },
      {
        name: "another database's forecast",
        instanceId: "windows-database",
        sourceInfo: { kind: "forecast", id: 18 },
      },
    ];
    for (const example of examples) {
      const workspace = {
        input: gameInput({ duration_seconds: 3000, resource_counts: Array(12).fill(25) }),
        limits: { time_limit_seconds: 120, max_iterations: null, seed: 7 },
        engineId: "cpp-local-search",
        instanceId: example.instanceId,
        profileId: 3,
        profileName: "Evening plan",
        selectedRunId: 18,
        preview: true,
        timelineSeconds: 30,
        sourceInfo: example.sourceInfo,
        undo: { input: gameInput() },
        view: "settings",
      };
      const original = structuredClone(workspace);

      const reconciled = reconcileWorkspace(workspace, "docker-database");

      assert.deepEqual(reconciled, {
        ...workspace,
        instanceId: "docker-database",
        profileId: null,
        selectedRunId: null,
        preview: false,
        timelineSeconds: 0,
        sourceInfo: { kind: "manual" },
        undo: null,
      }, example.name);
      assert.deepEqual(workspace, original, example.name);
    }
  });

  test("remembers the new database while retaining imported values and search settings", () => {
    const storage = memoryStorage();
    const imported = {
      input: gameInput({ duration_seconds: 3000, starting_path: [upgrade.cup, upgrade.finish] }),
      limits: { time_limit_seconds: 90, max_iterations: null, seed: 17 },
      instanceId: "windows-database",
      sourceInfo: { kind: "ocr", at: 1700000000000 },
    };
    writeWorkspace(storage, imported);

    const moved = reconcileWorkspace(readWorkspace(storage, gameInput()), "docker-database");
    writeWorkspace(storage, moved);
    const restored = readWorkspace(storage, gameInput());

    assert.equal(restored.instanceId, "docker-database");
    assert.deepEqual(restored.input, imported.input);
    assert.deepEqual(restored.limits, imported.limits);
    assert.deepEqual(restored.sourceInfo, imported.sourceInfo);
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
