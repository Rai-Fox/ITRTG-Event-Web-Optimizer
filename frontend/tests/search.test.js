import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { completedSearchSummary } from "../src/completedSearch.js";
import { optimizerStatus } from "../src/runPresentation.js";
import { buildScoreChart } from "../src/scoreHistory.js";
import { searchProgress } from "../src/searchProgress.js";
import { savedRun } from "./fixtures.js";

describe("Following a live search", () => {
  test("shows the current score, checked variants and time between server updates", () => {
    const run = savedRun({ status: "running" });

    const progress = searchProgress(run, { receivedAtMs: 1000, nowMs: 2500 });

    assert.equal(progress.score, 4.25);
    assert.equal(progress.iterations, 250);
    assert.equal(progress.elapsedSeconds, 11.5);
    assert.equal(progress.remainingSeconds, 18.5);
    assert.equal(progress.fraction, 11.5 / 30);
    assert.equal(progress.secondsSinceImprovement, 7.5);
  });

  test("does not rewind the displayed clock on a late heartbeat or disconnection", () => {
    const run = savedRun({ status: "running" });
    const before = searchProgress(run, { receivedAtMs: 1000, nowMs: 3000 });
    run.result.search_stats.elapsed_seconds = 11;

    const delayed = searchProgress(run, {
      receivedAtMs: 3000,
      nowMs: 3100,
      previousElapsedSeconds: before.elapsedSeconds,
    });
    const disconnected = searchProgress(run, {
      connected: false,
      nowMs: 90000,
      previousElapsedSeconds: delayed.elapsedSeconds,
    });

    assert.equal(before.elapsedSeconds, 12);
    assert.equal(delayed.elapsedSeconds, 12);
    assert.equal(disconnected.elapsedSeconds, 12);
    assert.equal(disconnected.secondsSinceImprovement, 8);
  });

  test("the improvement clock advances on a plateau and resets only for a new best", () => {
    const run = savedRun({ status: "running" });
    const first = searchProgress(run);
    run.result.search_stats.elapsed_seconds = 12;
    const plateau = searchProgress(run);
    run.score = 5;
    run.result.search_stats.last_improvement_elapsed_seconds = 11;
    const improved = searchProgress(run);
    const restored = searchProgress(structuredClone(run));

    assert.equal(first.secondsSinceImprovement, 6);
    assert.equal(plateau.secondsSinceImprovement, 8);
    assert.equal(improved.score, 5);
    assert.equal(improved.secondsSinceImprovement, 1);
    assert.equal(restored.secondsSinceImprovement, 1, "reloading uses the saved timestamp");
  });

  test("waits for telemetry while preserving a genuine zero score when it arrives", () => {
    const queued = { status: "queued", limits: { time_limit_seconds: 30 } };
    const empty = searchProgress(queued, { receivedAtMs: 0, nowMs: 90000 });
    const measured = searchProgress({
      ...queued,
      status: "running",
      score: 0,
      result: {
        search_stats: {
          elapsed_seconds: 0,
          iterations: 0,
          last_improvement_elapsed_seconds: 0,
        },
      },
    });

    assert.equal(empty.score, null);
    assert.equal(empty.fraction, null);
    assert.equal(empty.secondsSinceImprovement, null);
    assert.equal(measured.score, 0);
    assert.equal(measured.iterations, 0);
    assert.equal(measured.fraction, 0);
    assert.equal(measured.secondsSinceImprovement, 0);
  });

  test("shows finishing at the budget while retaining a final evaluation that ran longer", () => {
    const run = savedRun({ status: "running" });
    run.limits.time_limit_seconds = 10;
    run.result.search_stats.elapsed_seconds = 42;
    run.result.search_stats.elapsed_seconds_offset = 30;

    const progress = searchProgress(run, {
      receivedAtMs: 0,
      nowMs: 90000,
    });

    assert.equal(progress.status, "running");
    assert.equal(progress.finishing, true);
    assert.equal(progress.elapsedSeconds, 10);
    assert.equal(progress.totalElapsedSeconds, 42);
    assert.equal(progress.remainingSeconds, 0);
    assert.equal(progress.fraction, 1);
  });

  test("keeps cumulative variants and chart time while a continuation uses its own budget", () => {
    const run = savedRun({ status: "running", parent_run_id: 6 });
    Object.assign(run.result.search_stats, {
      elapsed_seconds: 70,
      elapsed_seconds_offset: 60,
      iterations: 1250,
      last_improvement_elapsed_seconds: 54,
    });

    const progress = searchProgress(structuredClone(run), { receivedAtMs: 1000, nowMs: 2500 });

    assert.equal(progress.iterations, 1250);
    assert.equal(progress.elapsedSeconds, 11.5);
    assert.equal(progress.totalElapsedSeconds, 71.5);
    assert.equal(progress.remainingSeconds, 18.5);
    assert.equal(progress.fraction, 11.5 / 30);
    assert.equal(progress.secondsSinceImprovement, 17.5);
  });
});

describe("Keeping a finished result visible", () => {
  test("retains saved points, history and the frozen improvement age after a search ends", () => {
    for (const status of ["completed", "stopped", "interrupted", "failed"]) {
      const run = savedRun({ status });
      run.limits.time_limit_seconds = 600;

      const summary = completedSearchSummary(structuredClone(run));

      assert.equal(summary.score, 4.25, status);
      assert.equal(summary.elapsedSeconds, 10, status);
      assert.equal(summary.secondsSinceImprovement, 6, status);
      assert.deepEqual(summary.history, run.result.search_stats.score_history, status);
    }
  });

  test("does not confuse a selected historical result with the active search", () => {
    const historical = savedRun();
    const active = savedRun({ id: 8, status: "running", score: 9 });

    assert.equal(searchProgress(historical), null);
    assert.equal(completedSearchSummary(historical, active), null);
    assert.equal(searchProgress(active).score, 9);
    assert.equal(optimizerStatus({
      connection: "connected",
      activeRun: null,
      selectedRun: historical,
    }), "ready");
    assert.equal(optimizerStatus({
      connection: "disconnected",
      activeRun: active,
    }), "disconnected");
  });

  test("shows an old zero-point result without inventing missing history or timing", () => {
    const summary = completedSearchSummary(savedRun({ score: 0, result: { score: 4 } }));

    assert.equal(summary.score, 0);
    assert.equal(summary.elapsedSeconds, null);
    assert.equal(summary.secondsSinceImprovement, null);
    assert.equal(buildScoreChart(summary.history, 0), null);
  });

  test("restores a continued result with total history and the latest search budget", () => {
    const run = savedRun({ parent_run_id: 6 });
    Object.assign(run.result.search_stats, {
      elapsed_seconds: 90,
      elapsed_seconds_offset: 60,
      iterations: 1250,
      last_improvement_elapsed_seconds: 84,
      score_history: [
        { elapsed_seconds: 0, score: 1 },
        { elapsed_seconds: 54, score: 4 },
        { elapsed_seconds: 84, score: 4.25 },
      ],
    });

    const summary = completedSearchSummary(structuredClone(run));
    const chart = buildScoreChart(summary.history, summary.elapsedSeconds);

    assert.equal(summary.elapsedSeconds, 90);
    assert.equal(summary.currentElapsedSeconds, 30);
    assert.equal(summary.limitSeconds, 30);
    assert.equal(summary.iterations, 1250);
    assert.equal(summary.secondsSinceImprovement, 6);
    assert.equal(chart.duration, 90);
    assert.deepEqual(chart.points.map(point => point.score), [1, 4, 4.25, 4.25]);
  });
});

describe("Displaying score changes", () => {
  test("shows improvements as steps and extends the last score across a plateau", () => {
    const history = [
      { elapsed_seconds: 0, score: 1 },
      { elapsed_seconds: 4, score: 3 },
    ];
    const original = structuredClone(history);

    const chart = buildScoreChart(history, 10, { width: 100, height: 100 });

    assert.deepEqual(
      chart.points.map(({ elapsed_seconds, score }) => [elapsed_seconds, score]),
      [[0, 1], [4, 3], [10, 3]],
    );
    assert.match(chart.path, /H 40 V /);
    assert.equal(chart.lastPoint.x, 100);
    assert.equal(chart.lastPoint.y, chart.points[1].y);
    assert.ok(chart.points[1].y < chart.points[0].y);
    assert.deepEqual(history, original);
  });

  test("keeps a flat zero-point score visible with finite coordinates", () => {
    const chart = buildScoreChart([{ elapsed_seconds: 0, score: 0 }], 20);

    assert.equal(chart.points[0].y, chart.lastPoint.y);
    assert.ok(chart.lastPoint.y > 0 && chart.lastPoint.y < chart.height);
    assert.doesNotMatch(chart.path, /NaN|Infinity/);
  });

  test("includes a final improvement recorded just after the search deadline", () => {
    const history = [
      { elapsed_seconds: 0, score: 1 },
      { elapsed_seconds: 10.05, score: 2 },
    ];

    const chart = buildScoreChart(history, 10);

    assert.equal(chart.duration, 10.05);
    assert.equal(chart.lastPoint.score, 2);
    assert.equal(chart.lastPoint.x, chart.width);
  });

  test("keeps earlier scores when a changed game state starts a lower scoring segment", () => {
    const history = [
      { elapsed_seconds: 0, score: 4 },
      { elapsed_seconds: 6, score: 10 },
      { elapsed_seconds: 10, score: 10 },
      { elapsed_seconds: 10, score: 1, segment_start: true },
      { elapsed_seconds: 10, score: 2 },
      { elapsed_seconds: 12, score: 1.5 },
      { elapsed_seconds: 13, score: 3 },
    ];
    const original = structuredClone(history);

    const chart = buildScoreChart(history, 15);

    assert.deepEqual(
      chart.points.map(({ elapsed_seconds, score }) => [elapsed_seconds, score]),
      [[0, 4], [6, 10], [10, 10], [10, 2], [13, 3], [15, 3]],
    );
    assert.equal(chart.path.match(/M /g).length, 2, "changed inputs are not joined to the old curve");
    assert.ok(chart.minScore < 2);
    assert.ok(chart.maxScore > 10);
    assert.equal(chart.lastPoint.x, chart.width);
    assert.deepEqual(history, original);
  });
});
