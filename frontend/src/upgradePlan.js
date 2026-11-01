const NUM_RESOURCES = 12;
const SAME_TIME_EPSILON = 1e-6;

export function buildUpgradePlan(purchases = [], startingLevels = [], { limit = 5 } = {}) {
  const levels = Array.from({ length: NUM_RESOURCES * 2 }, (_, index) => startingLevels[index] ?? 0);
  const batches = [];
  let current = null;
  let upgrades = null;

  for (const purchase of purchases) {
    const type = purchase.upgrade_type;
    const elapsed = purchase.elapsed_seconds;
    if (!Number.isInteger(type) || type < 0 || type >= NUM_RESOURCES * 2) continue;
    if (!Number.isFinite(elapsed) || elapsed < -SAME_TIME_EPSILON) continue;

    if (!current || Math.abs(current.elapsed_seconds - elapsed) > SAME_TIME_EPSILON) {
      current = {
        elapsed_seconds: Math.abs(elapsed) <= SAME_TIME_EPSILON ? 0 : elapsed,
        purchaseCount: 0,
        upgrades: [],
      };
      upgrades = new Map();
      batches.push(current);
    }

    const resourceIndex = type % NUM_RESOURCES;
    if (!upgrades.has(resourceIndex)) {
      const upgrade = {
        resourceIndex,
        fromLevel: levels[resourceIndex],
        fromSpeed: levels[resourceIndex + NUM_RESOURCES],
        level: levels[resourceIndex],
        speed: levels[resourceIndex + NUM_RESOURCES],
      };
      upgrades.set(resourceIndex, upgrade);
      current.upgrades.push(upgrade);
    }

    // Targets come from the simulated purchases, not an independent budget check.
    levels[type] = Number.isInteger(purchase.level_after) ? purchase.level_after : levels[type] + 1;
    const upgrade = upgrades.get(resourceIndex);
    upgrade.level = levels[resourceIndex];
    upgrade.speed = levels[resourceIndex + NUM_RESOURCES];
    current.purchaseCount += 1;
  }

  const now = batches[0]?.elapsed_seconds === 0 ? batches[0] : null;
  const count = Number.isFinite(limit) ? Math.max(0, Math.trunc(limit)) : 5;
  return {
    now,
    next: batches.filter((batch) => batch.elapsed_seconds > SAME_TIME_EPSILON).slice(0, count),
  };
}
