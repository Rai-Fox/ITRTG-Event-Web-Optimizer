function nonnegativeNumber(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

export function normalizeSearchLimits(limits = {}) {
  return { ...limits, max_iterations: null };
}

export function searchProgress(activeRun, { receivedAtMs, nowMs = Date.now(), connected = true, previousElapsedSeconds } = {}) {
  if (activeRun?.status !== "queued" && activeRun?.status !== "running") return null;
  const stats = activeRun.result?.search_stats;
  const budget = nonnegativeNumber(activeRun.limits?.time_limit_seconds);
  const serverElapsed = nonnegativeNumber(stats?.elapsed_seconds);
  const iterations = nonnegativeNumber(stats?.iterations);
  const score = activeRun.score ?? activeRun.result?.score;
  let elapsed = serverElapsed;
  if (elapsed !== null && activeRun.status === "running" && connected
      && Number.isFinite(receivedAtMs) && Number.isFinite(nowMs)) {
    elapsed += Math.max(0, nowMs - receivedAtMs) / 1000;
  }
  const previous = nonnegativeNumber(previousElapsedSeconds);
  if (elapsed !== null && activeRun.status === "running" && previous !== null) {
    elapsed = connected ? Math.max(previous, elapsed) : previous;
  }
  const hasBudget = budget !== null && budget > 0;
  if (elapsed !== null && hasBudget) elapsed = Math.min(budget, elapsed);
  const lastImprovement = nonnegativeNumber(stats?.last_improvement_elapsed_seconds);
  return {
    status: activeRun.status,
    score: typeof score === "number" && Number.isFinite(score) ? score : null,
    iterations: iterations === null ? null : Math.floor(iterations),
    elapsedSeconds: elapsed,
    limitSeconds: hasBudget ? budget : null,
    remainingSeconds: elapsed !== null && hasBudget ? Math.max(0, budget - elapsed) : null,
    fraction: elapsed !== null && hasBudget ? elapsed / budget : null,
    finishing: activeRun.status === "running" && elapsed !== null && hasBudget && elapsed >= budget,
    hasStats: serverElapsed !== null,
    secondsSinceImprovement: elapsed !== null && lastImprovement !== null ? Math.max(0, elapsed - lastImprovement) : null,
  };
}
