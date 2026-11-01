import { normalizeSearchLimits } from "./searchProgress.js";

export const WORKSPACE_KEY = "itrtg:halloween2026:workspace:v1";
export const DEFAULT_LIMITS = { time_limit_seconds: 30, max_iterations: null, seed: null };
export const ACTIVE_STATUSES = new Set(["queued", "running"]);

export function readWorkspace(storage, defaultInput) {
  const fallback = { input: structuredClone(defaultInput), limits: { ...DEFAULT_LIMITS }, recordedAt: Date.now() };
  try {
    const saved = JSON.parse(storage.getItem(WORKSPACE_KEY));
    if (saved?.version !== 1 || saved.input?.current_levels?.length !== 25 || saved.input?.resource_counts?.length !== 12) return fallback;
    return { ...saved, input: { ...fallback.input, ...saved.input }, limits: normalizeSearchLimits({ ...DEFAULT_LIMITS, ...saved.limits }) };
  } catch {
    return fallback;
  }
}

export function writeWorkspace(storage, workspace) {
  storage.setItem(WORKSPACE_KEY, JSON.stringify({ ...workspace, version: 1 }));
}

export function inputAtTimeline(input, timeline) {
  const elapsedHours = timeline.elapsed_seconds / 3600;
  return {
    ...structuredClone(input),
    duration_seconds: Math.max(1, Math.round(input.duration_seconds - timeline.elapsed_seconds)),
    current_levels: timeline.levels.map(Number),
    resource_counts: timeline.resources.map((value) => Math.max(0, Number(value))),
    starting_path: [],
    busy_times: input.busy_times
      .filter((busy) => busy.end_hour > elapsedHours)
      .map((busy) => ({ start_hour: Math.max(0, busy.start_hour - elapsedHours), end_hour: busy.end_hour - elapsedHours })),
  };
}
