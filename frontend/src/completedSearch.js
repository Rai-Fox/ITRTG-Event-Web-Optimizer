const TERMINAL_STATUSES = new Set(["completed", "stopped", "failed", "interrupted"]);

function nonnegativeNumber(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

export function completedSearchSummary(run, activeRun) {
  if (activeRun?.status === "queued" || activeRun?.status === "running"
      || !TERMINAL_STATUSES.has(run?.status) || !run.result) return null;
  const stats = run.result.search_stats;
  const elapsedSeconds = nonnegativeNumber(stats?.elapsed_seconds);
  const elapsedOffset = nonnegativeNumber(stats?.elapsed_seconds_offset) ?? 0;
  const lastImprovement = nonnegativeNumber(stats?.last_improvement_elapsed_seconds);
  const score = typeof run.score === "number" && Number.isFinite(run.score) ? run.score : run.result.score;
  return {
    score: typeof score === "number" && Number.isFinite(score) ? score : null,
    elapsedSeconds,
    currentElapsedSeconds: elapsedSeconds === null ? null : Math.max(0, elapsedSeconds - elapsedOffset),
    limitSeconds: nonnegativeNumber(run.limits?.time_limit_seconds),
    iterations: nonnegativeNumber(stats?.iterations),
    secondsSinceImprovement: elapsedSeconds !== null && lastImprovement !== null
      ? Math.max(0, elapsedSeconds - lastImprovement) : null,
    history: stats?.score_history,
  };
}
