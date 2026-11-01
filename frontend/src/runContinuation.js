const CONTINUABLE_STATUSES = new Set(["completed", "stopped", "interrupted"]);

function withoutStartingPath(input) {
  const { starting_path: _startingPath, ...settings } = input || {};
  return settings;
}

function normalize(value) {
  if (Array.isArray(value)) return value.map(normalize);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, normalize(value[key])]));
  }
  return value;
}

export function settingsMatch(input, other) {
  return JSON.stringify(normalize(withoutStartingPath(input)))
    === JSON.stringify(normalize(withoutStartingPath(other)));
}

export function getContinuationSeed({ input, engineId, engineVersion, run, runStatus, bestPath }) {
  if (!CONTINUABLE_STATUSES.has(runStatus) || !run || !Array.isArray(bestPath) || !bestPath.length) return null;
  if (run.engine_id !== engineId || engineVersion && run.engine_version !== engineVersion) return null;
  if (bestPath.some(step => !Number.isInteger(step) || step < 0 || step > 24)) return null;
  if (settingsMatch(input, run.input)) return { mode: "same", path: [...bestPath], removedPurchases: 0 };
  if (input.starting_path?.length) return null;

  const before = run.input?.current_levels;
  const current = input.current_levels;
  const validLevels = levels => Array.isArray(levels) && levels.length === 25
    && levels.every(level => Number.isInteger(level) && level >= 0);
  if (!validLevels(before) || !validLevels(current)) return null;
  if (current.slice(0, 24).some((level, index) => level < before[index])) return null;
  if (!Number.isFinite(input.duration_seconds) || input.duration_seconds <= 0
      || !Number.isFinite(run.input.duration_seconds) || input.duration_seconds > run.input.duration_seconds) return null;

  const levels = [...before];
  let removedPurchases = 0;
  for (const step of bestPath) {
    if (step === 24) break;
    const target = step >= 12 ? Math.min(10, levels[step] + 1) : levels[step] + 1;
    levels[step] = target;
    if (current[step] < target) break;
    removedPurchases += 1;
  }
  const remaining = bestPath.slice(removedPurchases);
  return { mode: "trimmed", path: remaining.length ? remaining : [24], removedPurchases };
}

export function canContinueOptimization(options) {
  return getContinuationSeed(options) !== null;
}

export function buildRunRequest(options, limits) {
  const seed = getContinuationSeed(options);
  return {
    engine_id: options.engineId,
    input: seed ? { ...options.input, starting_path: seed.path } : options.input,
    limits,
    ...(seed ? { parent_run_id: options.run.id } : {}),
  };
}
