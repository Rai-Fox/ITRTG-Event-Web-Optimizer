import { ACTIVE_STATUSES } from "./workspace.js";

function wholeNonnegative(value) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(0, Math.trunc(number)) : 0;
}

export function durationParts(seconds) {
  const total = wholeNonnegative(seconds);
  return {
    days: Math.floor(total / 86400),
    hours: Math.floor(total / 3600) % 24,
    minutes: Math.floor(total / 60) % 60,
    seconds: total % 60,
  };
}

export function durationFromParts(parts) {
  return wholeNonnegative(parts.days) * 86400
    + Math.min(23, wholeNonnegative(parts.hours)) * 3600
    + Math.min(59, wholeNonnegative(parts.minutes)) * 60
    + Math.min(59, wholeNonnegative(parts.seconds));
}

export function formatScore(score, locale = "en-US") {
  if (typeof score !== "number" || !Number.isFinite(score)) return "—";
  const magnitude = Math.abs(score);
  const scientific = magnitude > 0 && (magnitude < 0.001 || magnitude >= 1000000000);
  return new Intl.NumberFormat(locale, {
    maximumFractionDigits: 3,
    notation: scientific ? "scientific" : "standard",
  }).format(score === 0 ? 0 : score);
}

export function optimizerStatus({ connection, activeRun, starting = false }) {
  if (connection === "disconnected") return "disconnected";
  if (connection !== "connected") return "connecting";
  return starting || ACTIVE_STATUSES.has(activeRun?.status) ? "running" : "ready";
}
