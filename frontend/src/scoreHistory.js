function finite(value) {
  return typeof value === "number" && Number.isFinite(value);
}

export function buildScoreChart(history, elapsedSeconds, { width = 640, height = 130 } = {}) {
  const ordered = (Array.isArray(history) ? history : [])
    .filter((point) => finite(point?.elapsed_seconds) && point.elapsed_seconds >= 0 && finite(point.score))
    .map(({ elapsed_seconds, score }) => ({ elapsed_seconds, score }))
    .sort((a, b) => a.elapsed_seconds - b.elapsed_seconds);
  const samples = [];
  for (const point of ordered) {
    const previous = samples.at(-1);
    if (previous && point.score < previous.score) continue;
    if (previous?.elapsed_seconds === point.elapsed_seconds) samples[samples.length - 1] = point;
    else samples.push(point);
  }
  if (!samples.length) return null;
  width = finite(width) && width > 0 ? width : 640;
  height = finite(height) && height > 0 ? height : 130;
  const duration = Math.max(finite(elapsedSeconds) ? elapsedSeconds : 0, samples.at(-1).elapsed_seconds);
  if (samples.at(-1).elapsed_seconds < duration) {
    samples.push({ elapsed_seconds: duration, score: samples.at(-1).score });
  }
  const firstScore = samples[0].score;
  const lastScore = samples.at(-1).score;
  const padding = lastScore > firstScore ? (lastScore - firstScore) * 0.1 : Math.max(1, Math.abs(firstScore) * 0.05);
  const minScore = firstScore - padding;
  const maxScore = lastScore + padding;
  const points = samples.map((point) => ({
    ...point,
    x: point.elapsed_seconds / (duration || 1) * width,
    y: (maxScore - point.score) / (maxScore - minScore) * height,
  }));
  const coordinate = (value) => Math.round(value * 1000) / 1000;
  const path = points.map((point, index) => index === 0
    ? `M ${coordinate(point.x)} ${coordinate(point.y)}`
    : `H ${coordinate(point.x)} V ${coordinate(point.y)}`).join(" ");
  return { path, points, minScore, maxScore, duration, width, height, lastPoint: points.at(-1) };
}
