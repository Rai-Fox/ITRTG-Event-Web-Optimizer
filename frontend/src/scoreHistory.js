function finite(value) {
  return typeof value === "number" && Number.isFinite(value);
}

export function buildScoreChart(history, elapsedSeconds, { width = 640, height = 130 } = {}) {
  const ordered = (Array.isArray(history) ? history : [])
    .filter((point) => finite(point?.elapsed_seconds) && point.elapsed_seconds >= 0 && finite(point.score))
    .map(({ elapsed_seconds, score, segment_start }) => ({
      elapsed_seconds, score, segment_start: segment_start === true,
    }))
    .sort((a, b) => a.elapsed_seconds - b.elapsed_seconds);
  const samples = [];
  for (const point of ordered) {
    const previous = samples.at(-1);
    if (previous && !point.segment_start && point.score < previous.score) continue;
    if (previous?.elapsed_seconds === point.elapsed_seconds && !point.segment_start) {
      samples[samples.length - 1] = { ...point, segment_start: previous.segment_start };
    }
    else samples.push(point);
  }
  if (!samples.length) return null;
  width = finite(width) && width > 0 ? width : 640;
  height = finite(height) && height > 0 ? height : 130;
  const duration = Math.max(finite(elapsedSeconds) ? elapsedSeconds : 0, samples.at(-1).elapsed_seconds);
  if (samples.at(-1).elapsed_seconds < duration) {
    samples.push({ elapsed_seconds: duration, score: samples.at(-1).score });
  }
  const lowestScore = Math.min(...samples.map(point => point.score));
  const highestScore = Math.max(...samples.map(point => point.score));
  const padding = highestScore > lowestScore
    ? (highestScore - lowestScore) * 0.1 : Math.max(1, Math.abs(lowestScore) * 0.05);
  const minScore = lowestScore - padding;
  const maxScore = highestScore + padding;
  const points = samples.map((point) => ({
    ...point,
    x: point.elapsed_seconds / (duration || 1) * width,
    y: (maxScore - point.score) / (maxScore - minScore) * height,
  }));
  const coordinate = (value) => Math.round(value * 1000) / 1000;
  const path = points.map((point, index) => index === 0 || point.segment_start
    ? `M ${coordinate(point.x)} ${coordinate(point.y)}`
    : `H ${coordinate(point.x)} V ${coordinate(point.y)}`).join(" ");
  return { path, points, minScore, maxScore, duration, width, height, lastPoint: points.at(-1) };
}
