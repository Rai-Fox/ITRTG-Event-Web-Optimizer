import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  buildRunRequest,
  getContinuationSeed,
  settingsMatch,
} from "../src/runContinuation.js";
import { buildUpgradePlan } from "../src/upgradePlan.js";
import { gameInput, purchase, savedRun, upgrade } from "./fixtures.js";

function continuation(input = gameInput()) {
  const run = savedRun();
  return {
    input,
    run,
    engineId: run.engine_id,
    engineVersion: run.engine_version,
    runStatus: run.status,
    bestPath: run.result.path,
  };
}

describe("Continuing a saved plan", () => {
  test("links a continuation to its saved run and reuses the best path", () => {
    for (const status of ["completed", "stopped", "interrupted"]) {
      const request = continuation();
      request.runStatus = status;
      request.run.status = status;

      const limits = { time_limit_seconds: 60, max_iterations: null };
      const prepared = buildRunRequest(request, limits);

      assert.equal(prepared.parent_run_id, request.run.id, status);
      assert.equal(prepared.engine_id, request.engineId, status);
      assert.deepEqual(prepared.limits, limits, status);
      assert.deepEqual(prepared.input.starting_path, request.bestPath, status);
      assert.notEqual(prepared.input.starting_path, request.bestPath, status);
    }
  });

  test("keeps the previous plan when only resources and remaining time change", () => {
    const current = gameInput({
      duration_seconds: 3000,
      resource_counts: Array(12).fill(125),
    });
    const request = continuation(current);

    const prepared = buildRunRequest(request);

    assert.equal(prepared.parent_run_id, request.run.id);
    assert.deepEqual(prepared.input.starting_path, request.bestPath);
    assert.equal(prepared.input.duration_seconds, 3000);
    assert.deepEqual(prepared.input.resource_counts, Array(12).fill(125));
    assert.equal(settingsMatch(request.input, request.run.input), false);
  });

  test("removes completed level and speed purchases without changing the saved plan", () => {
    const current = gameInput({ duration_seconds: 3000 });
    current.current_levels[upgrade.graveStone] = 2;
    current.current_levels[upgrade.graveStoneSpeed] = 1;
    const request = continuation(current);
    const before = structuredClone(request);

    const seed = getContinuationSeed(request);
    const prepared = buildRunRequest(request);

    assert.deepEqual(seed, {
      mode: "trimmed",
      path: [upgrade.cup, upgrade.finish],
      removedPurchases: 3,
    });
    assert.equal(prepared.parent_run_id, request.run.id);
    assert.deepEqual(prepared.input.starting_path, [upgrade.cup, upgrade.finish]);
    seed.path.push(upgrade.spiderWeb);
    assert.deepEqual(request, before);
  });

  test("stops trimming at the first unfinished purchase even if later purchases were made", () => {
    const current = gameInput({ duration_seconds: 3000 });
    current.current_levels[upgrade.graveStone] = 1;
    current.current_levels[upgrade.graveStoneSpeed] = 1;

    const seed = getContinuationSeed(continuation(current));

    assert.deepEqual(seed.path, [
      upgrade.graveStone,
      upgrade.graveStoneSpeed,
      upgrade.cup,
      upgrade.finish,
    ]);
    assert.equal(seed.removedPurchases, 1);
  });

  test("uses the current state as a seed when every old purchase is complete", () => {
    const current = gameInput({ duration_seconds: 3000 });
    current.current_levels[upgrade.graveStone] = 1;
    const request = continuation(current);
    request.bestPath = [upgrade.graveStone];

    assert.deepEqual(buildRunRequest(request).input.starting_path, [upgrade.finish]);
  });

  test("does not carry a plan into a reset game or an extended event", () => {
    const reset = continuation(gameInput({ duration_seconds: 3000 }));
    reset.input.current_levels[upgrade.spiderWeb] = 0;
    const extended = continuation(gameInput({ duration_seconds: 7200 }));

    assert.equal(getContinuationSeed(reset), null, "levels decreased");
    assert.equal(getContinuationSeed(extended), null, "event time increased");
    assert.equal(buildRunRequest(reset).parent_run_id, undefined, "a new search has no parent history");
    assert.deepEqual(buildRunRequest(reset).input.starting_path, []);
  });

  test("does not reuse a plan from an incompatible engine", () => {
    const incompatibleEngines = [
      { name: "different engine", engineId: "another-engine", engineVersion: "1" },
      { name: "different version", engineId: "cpp-local-search", engineVersion: "2" },
    ];

    for (const { name, ...engine } of incompatibleEngines) {
      const request = { ...continuation(), ...engine };

      assert.equal(getContinuationSeed(request), null, name);
    }
  });

  test("waits for the current search to finish before offering continuation", () => {
    const request = continuation();
    request.runStatus = "running";

    assert.equal(getContinuationSeed(request), null);
  });

  test("keeps an explicitly entered starting path for the updated state", () => {
    const manual = gameInput({
      duration_seconds: 3000,
      starting_path: [upgrade.cup, upgrade.finish],
    });

    const prepared = buildRunRequest(continuation(manual));

    assert.deepEqual(prepared.input, manual);
    assert.equal(prepared.parent_run_id, undefined);
  });
});

describe("Showing actionable upgrades", () => {
  test("groups immediate purchases into final level and speed targets", () => {
    const purchases = [
      purchase(upgrade.spiderWeb, 0, 2),
      purchase(upgrade.spiderWeb, 0, 3),
      purchase(upgrade.spiderWebSpeed, 0.0000005, 1),
      purchase(upgrade.graveStone, 0, 1),
      purchase(upgrade.finish, 0, 0),
    ];

    const plan = buildUpgradePlan(purchases, gameInput().current_levels);

    assert.equal(plan.now.purchaseCount, 4);
    assert.deepEqual(plan.now.upgrades, [
      {
        resourceIndex: upgrade.spiderWeb,
        fromLevel: 1,
        fromSpeed: 0,
        level: 3,
        speed: 1,
      },
      {
        resourceIndex: upgrade.graveStone,
        fromLevel: 0,
        fromSpeed: 0,
        level: 1,
        speed: 0,
      },
    ]);
    assert.deepEqual(plan.next, []);
  });

  test("shows five future purchase moments with cumulative targets", () => {
    const levels = gameInput().current_levels;
    const purchases = [
      purchase(upgrade.spiderWebSpeed, 60, 1),
      purchase(upgrade.spiderWeb, 60, 2),
      ...Array.from({ length: 6 }, (_, index) => (
        purchase(upgrade.spiderWeb, 120 + index * 60, index + 3)
      )),
    ];
    const before = structuredClone({ levels, purchases });

    const plan = buildUpgradePlan(purchases, levels);

    assert.equal(plan.now, null);
    assert.deepEqual(
      plan.next.map(batch => batch.elapsed_seconds),
      [60, 120, 180, 240, 300],
    );
    assert.deepEqual(plan.next[0].upgrades, [{
      resourceIndex: upgrade.spiderWeb,
      fromLevel: 1,
      fromSpeed: 0,
      level: 2,
      speed: 1,
    }]);
    assert.deepEqual(plan.next[1].upgrades, [{
      resourceIndex: upgrade.spiderWeb,
      fromLevel: 2,
      fromSpeed: 1,
      level: 3,
      speed: 1,
    }]);
    assert.deepEqual({ levels, purchases }, before);
  });

  test("shows no upgrades for an empty plan", () => {
    assert.deepEqual(buildUpgradePlan([], gameInput().current_levels), { now: null, next: [] });
  });
});
