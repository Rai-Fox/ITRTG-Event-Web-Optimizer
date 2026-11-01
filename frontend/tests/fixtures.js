export const upgrade = {
  graveStone: 0,
  spiderWeb: 1,
  cup: 2,
  graveStoneSpeed: 12,
  spiderWebSpeed: 13,
  finish: 24,
};

export function gameInput(overrides = {}) {
  return {
    duration_seconds: 3600,
    current_levels: [0, 1, ...Array(23).fill(0)],
    resource_counts: Array(12).fill(0),
    unlocked_pets: 0,
    dungeon_levels: 0,
    adventure_level: 0,
    weights: {
      event_currency: 0.001,
      free_exp: 0.00006,
      pet_stones: 0.000035,
      research_points: 0.00002,
      growth: 0.00008,
    },
    busy_times: [],
    starting_path: [],
    ...overrides,
  };
}

export function savedRun(overrides = {}) {
  return {
    id: 7,
    engine_id: "cpp-local-search",
    engine_version: "1",
    status: "completed",
    input: gameInput(),
    limits: { time_limit_seconds: 30, max_iterations: null },
    score: 4.25,
    result: {
      score: 4.25,
      path: [
        upgrade.graveStone,
        upgrade.graveStone,
        upgrade.graveStoneSpeed,
        upgrade.cup,
        upgrade.finish,
      ],
      search_stats: {
        elapsed_seconds: 10,
        iterations: 250,
        last_improvement_elapsed_seconds: 4,
        score_history: [
          { elapsed_seconds: 0, score: 1 },
          { elapsed_seconds: 4, score: 4.25 },
        ],
      },
    },
    ...overrides,
  };
}

export function memoryStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
}

export function purchase(type, seconds, level) {
  return { upgrade_type: type, elapsed_seconds: seconds, level_after: level };
}
