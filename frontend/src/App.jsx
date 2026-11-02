import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { buildRunRequest, getContinuationSeed, settingsMatch } from "./runContinuation.js";
import { ACTIVE_STATUSES, DEFAULT_LIMITS, readWorkspace, writeWorkspace, reconcileWorkspace, inputAtTimeline, readChecklist, writeChecklist } from "./workspace.js";
import { useI18n } from "./I18nProvider.jsx";
import { translate, localeFor } from "./i18n.js";
import { durationParts, durationFromParts, formatScore, optimizerStatus } from "./runPresentation.js";
import { buildUpgradePlan } from "./upgradePlan.js";
import { searchProgress, normalizeSearchLimits } from "./searchProgress.js";
import { buildScoreChart } from "./scoreHistory.js";
import { completedSearchSummary } from "./completedSearch.js";
import { DEFAULT_OCR_ENGINE, readOcrEngine, writeOcrEngine } from "./ocrPreferences.js";
const PAGE_SIZE = 10;
const resourceLabels = ["Grave Stone", "Spider Web", "Cup", "Moon", "Candle", "Broom", "Free Exp", "Skull", "Pet Stones", "Research Points", "Witch Hat", "Growth"];
const weightKeys = ["event_currency", "free_exp", "pet_stones", "research_points", "growth"];
const weightLabels = {
  event_currency: "Witch Hat",
  free_exp: "Free Exp",
  pet_stones: "Pet Stones",
  research_points: "Research Points",
  growth: "Growth"
};
const statusLabels = {
  idle: "Ready",
  queued: "Queued",
  running: "Running",
  completed: "Search finished",
  stopped: "Stopped",
  failed: "Failed",
  interrupted: "Interrupted"
};
const defaultInput = {
  duration_seconds: 14 * 24 * 3600,
  current_levels: [0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
  resource_counts: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
  unlocked_pets: 0,
  dungeon_levels: 0,
  adventure_level: 0,
  weights: {
    event_currency: 0.001,
    free_exp: 0.00006,
    pet_stones: 0.000035,
    research_points: 0.00002,
    growth: 0.00008
  },
  busy_times: [],
  starting_path: []
};
function formatTime(seconds, language) {
  const parts = durationParts(Math.ceil(Number(seconds) || 0));
  const units = [["days", "{0}d"], ["hours", "{0}h"], ["minutes", "{0}m"], ["seconds", "{0}s"]];
  return units.filter(([key]) => parts[key] > 0).map(([key, label]) => translate(label, language, [parts[key]])).join(" ") || translate("{0}s", language, [0]);
}
function DurationInputs({ value, onChange, label }) {
  const { t } = useI18n();
  const parts = durationParts(value);
  const units = [["days", t("Days")], ["hours", t("Hours")], ["minutes", t("Minutes")], ["seconds", t("Seconds")]];
  return <div className="duration-field"><span>{label}</span><div className="duration-inputs">{units.map(([key, name]) => <label key={key}>{name}<input aria-label={`${label} ${name}`} type="number" min="0" max={key === "days" ? undefined : key === "hours" ? 23 : 59} value={parts[key]} onChange={(event) => onChange(durationFromParts({ ...parts, [key]: event.target.value }))} /></label>)}</div></div>;
}
function UpgradeBatch({ batch }) {
  return <div className="upgrade-targets">{batch.upgrades.map((upgrade) => <div className="upgrade-target" key={upgrade.resourceIndex}><strong>{resourceLabels[upgrade.resourceIndex]}</strong><span>Level {upgrade.fromLevel !== upgrade.level && <>{upgrade.fromLevel} → </>}<b>{upgrade.level}</b></span><span>Speed {upgrade.fromSpeed !== upgrade.speed && <>{upgrade.fromSpeed} → </>}<b>{upgrade.speed}</b></span></div>)}</div>;
}
function FullPlan({ instanceId, run, groups, purchases }) {
  const { t, language } = useI18n();
  const [checked, setChecked] = useState(() => readChecklist(window.localStorage, instanceId, run.id));
  const [saveFailed, setSaveFailed] = useState(false);
  const editable = !ACTIVE_STATUSES.has(run.status);
  const completed = purchases.filter(purchase => checked.includes(purchase.index)).length;
  function toggle(index) {
    const next = checked.includes(index) ? checked.filter(item => item !== index) : [...checked, index];
    setChecked(next);
    try {
      writeChecklist(window.localStorage, instanceId, run.id, next);
      setSaveFailed(false);
    } catch {
      setSaveFailed(true);
    }
  }
  return <details className="technical-details"><summary>{t("Full plan ·")} {purchases.length} {t("purchases")} · {t("{0} done", [completed])}</summary>
    <p>{editable ? t("Checks are saved in this browser for this plan.") : t("Checks are available when the search finishes.")}</p>
    {saveFailed && <p role="alert">{t("Could not save checkmarks in this browser.")}</p>}
    {groups.map(group => <details className="purchase-day" key={group.day}><summary>{t("Day")} {group.day + 1} · {t("{0} of {1} done", [group.purchases.filter(purchase => checked.includes(purchase.index)).length, group.purchases.length])}</summary>{group.purchases.map(purchase => <label className={`purchase-row${checked.includes(purchase.index) ? " purchase-done" : ""}`} key={purchase.index}><input type="checkbox" disabled={!editable} checked={checked.includes(purchase.index)} onChange={() => toggle(purchase.index)} /><span>{formatTime(purchase.elapsed_seconds, language)}</span><strong>{purchase.upgrade_name}</strong><small>{t("level")} {purchase.level_after}</small></label>)}</details>)}
  </details>;
}
function ScoreHistoryChart({ history, elapsedSeconds }) {
  const { t, language } = useI18n();
  const chart = useMemo(() => buildScoreChart(history, elapsedSeconds), [history, elapsedSeconds]);
  if (!chart) return null;
  return <div className="score-history">
    <h4>{t("Best score over time")}</h4>
    <div className="score-chart">
      <div className="score-chart-values" aria-hidden="true"><span>{formatScore(chart.maxScore, localeFor(language))}</span><span>{formatScore((chart.minScore + chart.maxScore) / 2, localeFor(language))}</span><span>{formatScore(chart.minScore, localeFor(language))}</span></div>
      <svg viewBox={`0 0 ${chart.width} ${chart.height}`} preserveAspectRatio="none" role="img" aria-label={t("Best score over time")}>
        <title>{t("Best score over time")}</title>
        {[0, 0.5, 1].map(fraction => <line className="score-chart-grid" key={fraction} x1="0" x2={chart.width} y1={fraction * chart.height} y2={fraction * chart.height} vectorEffect="non-scaling-stroke" />)}
        <path className="score-chart-line" d={chart.path} vectorEffect="non-scaling-stroke" />
        {chart.points.map((point, index) => <circle key={index} cx={point.x} cy={point.y} r="5" fill="transparent"><title>{t("{0}: {1} points", [t("{0}s", [formatNumber(point.elapsed_seconds, 1, language)]), formatScore(point.score, localeFor(language))])}</title></circle>)}
      </svg>
      <div className="score-chart-times" aria-hidden="true"><span>{t("{0}s", [0])}</span><span>{formatTime(chart.duration, language)}</span></div>
    </div>
  </div>;
}
function ActiveSearch({ run, connection, onShow, onStop }) {
  const { t, language } = useI18n();
  const receivedAtMs = useMemo(() => Date.now(), [run]);
  const [nowMs, setNowMs] = useState(Date.now);
  const previousElapsed = useRef(null);
  useEffect(() => {
    const timer = setInterval(() => setNowMs(Date.now()), 250);
    return () => clearInterval(timer);
  }, []);
  const progress = searchProgress(run, { receivedAtMs, nowMs, previousElapsedSeconds: previousElapsed.current, connected: connection === "connected" });
  if (!progress) return null;
  previousElapsed.current = progress.elapsedSeconds;
  const heading = connection !== "connected" ? t("Waiting for connection")
    : run.status === "queued" || !progress.hasStats ? t("Preparing search…")
    : progress.finishing ? t("Finishing search…") : t("Searching for a plan");
  return <section className="active-search" aria-label={t("Search progress")}>
    <div className="search-heading"><h3>{heading}</h3><div className="actions"><button className="button ghost" onClick={onShow}>{t("Show")}</button><button className="button danger" onClick={onStop}>{t("Stop search")}</button></div></div>
    <div className="search-metrics">
      <div className="search-score"><span>{t("Optimization points")}</span><strong>{formatScore(progress.score, localeFor(language))}</strong></div>
      <div><span>{t("Time used")}</span><strong>{progress.elapsedSeconds == null ? "—" : t("{0} of {1}", [formatTime(Math.floor(progress.elapsedSeconds), language), formatTime(progress.limitSeconds, language)])}</strong>{progress.totalElapsedSeconds > progress.elapsedSeconds && <span>{t("Total: {0}", [formatTime(Math.floor(progress.totalElapsedSeconds), language)])}</span>}</div>
      <div><span>{t("Variants checked")}</span><strong>{progress.iterations == null ? "—" : formatNumber(progress.iterations, 0, language)}</strong></div>
      <div><span>{t("Since last improvement")}</span><strong>{progress.secondsSinceImprovement == null ? "—" : formatTime(Math.floor(progress.secondsSinceImprovement), language)}</strong></div>
    </div>
    <progress aria-label={t("Search time used")} max="1" value={progress.fraction ?? undefined} />
    <ScoreHistoryChart history={run.result?.search_stats?.score_history} elapsedSeconds={progress.totalElapsedSeconds} />
  </section>;
}
function CompletedSearch({ run, summary }) {
  const { t, language } = useI18n();
  return <section className="active-search completed-search" aria-label={t("Search result")}>
    <div className="search-heading"><h3>{t("Run #{0}", [run.id])} · {t(statusLabels[run.status])}</h3></div>
    <div className="search-metrics">
      <div className="search-score"><span>{t("Optimization points")}</span><strong>{formatScore(summary.score, localeFor(language))}</strong></div>
      {summary.currentElapsedSeconds != null && <div><span>{t("Time used")}</span><strong>{summary.limitSeconds == null ? formatTime(Math.floor(summary.currentElapsedSeconds), language) : t("{0} of {1}", [formatTime(Math.floor(summary.currentElapsedSeconds), language), formatTime(summary.limitSeconds, language)])}</strong>{summary.elapsedSeconds > summary.currentElapsedSeconds && <span>{t("Total: {0}", [formatTime(Math.floor(summary.elapsedSeconds), language)])}</span>}</div>}
      {summary.iterations != null && <div><span>{t("Variants checked")}</span><strong>{formatNumber(summary.iterations, 0, language)}</strong></div>}
      {summary.secondsSinceImprovement != null && <div><span>{t("Since last improvement")}</span><strong>{formatTime(Math.floor(summary.secondsSinceImprovement), language)}</strong></div>}
    </div>
    <ScoreHistoryChart history={summary.history} elapsedSeconds={summary.elapsedSeconds} />
  </section>;
}
function formatNumber(value, fractionDigits = 0, language) {
  return Number(value || 0).toLocaleString(localeFor(language), {
    maximumFractionDigits: fractionDigits
  });
}
async function request(path, options = {}) {
  const response = await fetch(path, {
    signal: AbortSignal.timeout(15000),
    headers: options.body instanceof FormData ? undefined : {
      "Content-Type": "application/json"
    },
    ...options
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const message = Array.isArray(body.detail) ? body.detail.map(item => item.msg).join("; ") : body.detail;
    const error = new Error(message || `Request failed: ${response.status}`);
    error.status = response.status;
    throw error;
  }
  if (response.status === 204) return null;
  return response.json();
}
function RunSettingsSummary({
  savedRun,
  compact = false
}) {
  const {
    t,
    language
  } = useI18n();
  if (!savedRun?.input) return null;
  const limits = savedRun.limits;
  return <div className={compact ? "run-settings compact" : "run-settings"}>
      {!compact && <h3>{t("Run settings")}</h3>}
      <div className="settings-summary">
        <span>{t("Engine")} <strong>{savedRun.engine_id} {savedRun.engine_version}</strong></span>
        <span>{t("Horizon")} <strong>{formatTime(savedRun.input.duration_seconds, language)}</strong></span>
        <span>{t("Limits")} <strong>{limits ? limits.max_iterations != null ? t("{0}s / {1} iterations", [limits.time_limit_seconds, formatNumber(limits.max_iterations, 0, language)]) : formatTime(limits.time_limit_seconds, language) : "—"}</strong></span>
        <span>{t("Game context")} <strong>{savedRun.input.unlocked_pets} {t("pets /")} {savedRun.input.dungeon_levels} {t("dungeon levels /")} {savedRun.input.adventure_level} {t("adventure level")}</strong></span>
      </div>
    </div>;
}
function Pagination({
  page,
  total,
  pageSize,
  onPageChange
}) {
  const {
    t,
    language
  } = useI18n();
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  return <div className="pagination">
      <span>{t("Page")} {page} {t("of")} {pageCount} {t("· total")} {total}</span>
      <div>
        <button className="button ghost" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>{t("Previous")}</button>
        <button className="button ghost" disabled={page >= pageCount} onClick={() => onPageChange(page + 1)}>{t("Next")}</button>
      </div>
    </div>;
}
function OcrImportDialog({
  onClose,
  onApply
}) {
  const {
    t,
    language
  } = useI18n();
  const [stage, setStage] = useState("upload");
  const [previewUrl, setPreviewUrl] = useState("");
  const [fileName, setFileName] = useState("");
  const [draft, setDraft] = useState(null);
  const [warnings, setWarnings] = useState([]);
  const [message, setMessage] = useState("");
  const [dragActive, setDragActive] = useState(false);
  const [applyTime, setApplyTime] = useState(false);
  const [ocrEngines, setOcrEngines] = useState([{ id: "tesseract", name: "Tesseract" }]);
  const [ocrEngine, setOcrEngine] = useState(DEFAULT_OCR_ENGINE);
  const [loadingEngines, setLoadingEngines] = useState(true);
  const ocrStorage = useMemo(() => {
    try { return window.localStorage; }
    catch { return null; }
  }, []);
  const inputRef = useRef(null);
  const previewRef = useRef("");
  const loading = loadingEngines || stage === "loading";
  useEffect(() => {
    let cancelled = false;
    request("/api/ocr/engines").then(available => {
      if (cancelled) return;
      setOcrEngines(available);
      setOcrEngine(readOcrEngine(ocrStorage, available));
    }).catch(() => {
      if (cancelled) return;
      setMessage("Could not load OCR engines.");
      setStage("error");
    }).finally(() => {
      if (!cancelled) setLoadingEngines(false);
    });
    return () => { cancelled = true; };
  }, [ocrStorage]);
  useEffect(() => () => {
    if (previewRef.current) URL.revokeObjectURL(previewRef.current);
  }, []);
  const handleFile = useCallback(async file => {
    if (!file || loading) return;
    if (!file.type.startsWith("image/")) {
      setMessage(t("Choose an image file."));
      setStage("error");
      return;
    }
    if (previewRef.current) URL.revokeObjectURL(previewRef.current);
    const nextPreview = URL.createObjectURL(file);
    previewRef.current = nextPreview;
    setPreviewUrl(nextPreview);
    setFileName(file.name || t("Clipboard screenshot"));
    setMessage("");
    setStage("loading");
    const body = new FormData();
    body.append("file", file, file.name || "clipboard.png");
    body.append("engine", ocrEngine);
    try {
      const parsed = await request("/api/ocr/parse", {
        method: "POST",
        body,
        // A screenshot can need up to six OCR passes, each with a 15-second limit.
        signal: AbortSignal.timeout(120000)
      });
      setDraft({
        levels: parsed.levels.map(value => value ?? ""),
        speeds: parsed.speeds.map(value => value ?? ""),
        counts: parsed.counts.map(value => value ?? ""),
        duration_seconds: parsed.duration_seconds ?? null
      });
      setApplyTime(parsed.duration_seconds != null);
      setWarnings(parsed.warnings || []);
      setStage("review");
    } catch (error) {
      setMessage(error.message);
      setStage("error");
    }
  }, [loading, ocrEngine, t]);
  useEffect(() => {
    const onKeyDown = event => {
      if (event.key === "Escape") onClose();
    };
    const onPaste = event => {
      const item = Array.from(event.clipboardData?.items || []).find(candidate => candidate.type.startsWith("image/"));
      if (!item) {
        setMessage(t("There is no image on the clipboard."));
        return;
      }
      event.preventDefault();
      handleFile(item.getAsFile());
    };
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("paste", onPaste);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("paste", onPaste);
    };
  }, [handleFile, onClose, t]);
  function updateValue(group, index, value) {
    setDraft(current => ({
      ...current,
      [group]: current[group].map((item, itemIndex) => itemIndex === index ? value : item)
    }));
  }
  function isFieldValid(value, maximum = null) {
    if (!/^\d+$/.test(String(value))) return false;
    const numeric = Number(value);
    return Number.isSafeInteger(numeric) && (maximum === null || numeric <= maximum);
  }
  const reviewValid = Boolean(draft) && draft.levels.every(value => isFieldValid(value)) && draft.speeds.every(value => isFieldValid(value, 10)) && draft.counts.every(value => isFieldValid(value));
  function applyDraft() {
    if (!reviewValid) return;
    onApply({
      levels: draft.levels.map(Number),
      speeds: draft.speeds.map(Number),
      counts: draft.counts.map(Number),
      duration_seconds: applyTime ? draft.duration_seconds : null
    });
  }
  return <div className="dialog-backdrop" role="presentation">
      <section className="dialog" role="dialog" aria-modal="true" aria-labelledby="ocr-title">
        <div className="dialog-header">
          <div>
            <span className="eyebrow">{t("Import")}</span>
            <h2 id="ocr-title">{t("Import game screenshot")}</h2>
            <p>{t("Upload the full 3×4 grid. Check and edit the values before applying.")}</p>
          </div>
          <button className="icon-button" onClick={onClose} aria-label={t("Close")}>×</button>
        </div>

        {ocrEngines.length > 1 && <label className="ocr-engine">
          {t("OCR engine")}
          <select value={ocrEngine} disabled={loading} onChange={event => {
            setOcrEngine(event.target.value);
            writeOcrEngine(ocrStorage, event.target.value);
          }}>
            {ocrEngines.map(engine => <option key={engine.id} value={engine.id}>{engine.name}</option>)}
          </select>
        </label>}

        <div className={`dropzone ${dragActive ? "active" : ""}`} onDragEnter={event => {
        event.preventDefault();
        setDragActive(true);
      }} onDragOver={event => event.preventDefault()} onDragLeave={() => setDragActive(false)} onDrop={event => {
        event.preventDefault();
        setDragActive(false);
        handleFile(event.dataTransfer.files?.[0]);
      }}>
          <input ref={inputRef} className="visually-hidden" type="file" accept="image/*" disabled={loading} onChange={event => handleFile(event.target.files?.[0])} />
          <div className="dropzone-icon">⌁</div>
          <div><strong>{t("Drop an image here")}</strong><span>{t("or press Ctrl+V to paste a screenshot")}</span></div>
          <button className="button secondary" disabled={loading} onClick={() => {
          if (inputRef.current) inputRef.current.value = "";
          inputRef.current?.click();
        }}>{t("Choose file")}</button>
        </div>

        {previewUrl && <div className="ocr-source">
            <img src={previewUrl} alt={t("Screenshot preview")} />
            <div><strong>{fileName}</strong><span>{t("The image is processed locally and is not stored.")}</span></div>
          </div>}
        {stage === "loading" && <div className="loading-line"><span />{t("Reading 12 resource cards…")}</div>}
        {stage === "error" && <div className="inline-message danger">{t(message)}</div>}

        {stage === "review" && draft && <>
            {warnings.length > 0 && <details className="ocr-warnings"><summary>{t("OCR needs review ·")} {warnings.length}</summary>{warnings.map((warning, index) => <p key={`${warning}-${index}`}>{t(warning)}</p>)}</details>}
            {draft.duration_seconds != null ? <section className="ocr-time"><label className="check-option"><input type="checkbox" checked={applyTime} onChange={(event) => setApplyTime(event.target.checked)} />{t("Apply event time from screenshot")}</label>{applyTime && <DurationInputs value={draft.duration_seconds} label={t("Time from screenshot")} onChange={(value) => setDraft({ ...draft, duration_seconds: value })} />}</section> : <small className="ocr-time-missing">{t("Event time not found; current time will be kept.")}</small>}
            <div className="ocr-table-wrap">
              <div className="ocr-table">
                <div className="ocr-row header"><span>{t("Resource")}</span><span>Level</span><span>Speed</span><span>Count</span></div>
                {resourceLabels.map((label, index) => <div className="ocr-row" key={label}>
                    <strong>{label}</strong>
                    <input aria-label={`${label} Level`} className={isFieldValid(draft.levels[index]) ? "" : "invalid"} type="number" min="0" value={draft.levels[index]} onChange={event => updateValue("levels", index, event.target.value)} />
                    <input aria-label={`${label} Speed`} className={isFieldValid(draft.speeds[index], 10) ? "" : "invalid"} type="number" min="0" max="10" value={draft.speeds[index]} onChange={event => updateValue("speeds", index, event.target.value)} />
                    <input aria-label={`${label} Count`} className={isFieldValid(draft.counts[index]) ? "" : "invalid"} type="number" min="0" value={draft.counts[index]} onChange={event => updateValue("counts", index, event.target.value)} />
                  </div>)}
              </div>
            </div>
            <div className="dialog-footer">
              <span>{reviewValid ? t("All 36 values are ready.") : t("Enter valid whole numbers in the highlighted fields.")}</span>
              <div className="actions"><button className="button ghost" onClick={onClose}>{t("Cancel")}</button><button className="button" disabled={!reviewValid} onClick={applyDraft}>{t("Apply state")}</button></div>
            </div>
          </>}
      </section>
    </div>;
}
export default function App() {
  const {
    t,
    language,
    setLanguage
  } = useI18n();
  const [restored] = useState(() => readWorkspace(window.localStorage, defaultInput));
  const [input, setInput] = useState(restored.input);
  const [limits, setLimits] = useState(restored.limits);
  const [engineId, setEngineId] = useState(restored.engineId || "cpp-local-search");
  const [instanceId, setInstanceId] = useState(restored.instanceId || null);
  const [instanceReady, setInstanceReady] = useState(false);
  const [profileId, setProfileId] = useState(restored.profileId || null);
  const [profileName, setProfileName] = useState(restored.profileName || t("My profile"));
  const [sourceInfo, setSourceInfo] = useState(restored.sourceInfo || { kind: "manual" });
  const [selectedRunId, setSelectedRunId] = useState(restored.selectedRunId || null);
  const [timelineSeconds, setTimelineSeconds] = useState(restored.timelineSeconds || 0);
  const [preview, setPreview] = useState(Boolean(restored.preview));
  const [view, setView] = useState(["dashboard", "runs", "settings"].includes(restored.view) ? restored.view : "dashboard");
  const [engines, setEngines] = useState([]);
  const [profiles, setProfiles] = useState([]);
  const [profilePage, setProfilePage] = useState(1);
  const [profileTotal, setProfileTotal] = useState(0);
  const [runs, setRuns] = useState([]);
  const [runPage, setRunPage] = useState(1);
  const [runTotal, setRunTotal] = useState(0);
  const [run, setRun] = useState(null);
  const [activeRun, setActiveRun] = useState(null);
  const [timeline, setTimeline] = useState(null);
  const [connection, setConnection] = useState("connecting");
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [ocrOpen, setOcrOpen] = useState(false);
  const [isStarting, setIsStarting] = useState(false);
  const [isSavingProfile, setIsSavingProfile] = useState(false);
  const [undo, setUndo] = useState(null);
  const startGuard = useRef(false);
  const instanceRef = useRef(null);
  const instanceCheckRef = useRef(null);
  const workspaceRef = useRef(null);
  workspaceRef.current = { instanceId, profileId, selectedRunId, preview, timelineSeconds, sourceInfo, undo };
  const confirmInstance = useCallback(() => {
    if (instanceCheckRef.current) return instanceCheckRef.current;
    instanceCheckRef.current = (async () => {
      try {
        const identity = await request("/api/instance");
        if (typeof identity?.id !== "string" || !identity.id) throw new Error("Invalid instance identity");
        const previous = workspaceRef.current;
        const reconciled = reconcileWorkspace(previous, identity.id);
        instanceRef.current = identity.id;
        if (reconciled !== previous) {
          workspaceRef.current = reconciled;
          setInstanceId(identity.id);
          setProfileId(null);
          setSelectedRunId(null);
          setPreview(false);
          setTimelineSeconds(0);
          setSourceInfo(reconciled.sourceInfo);
          setUndo(null);
          setRun(null);
          setActiveRun(null);
          setTimeline(null);
          setProfiles([]);
          setProfileTotal(0);
          setProfilePage(1);
          setRuns([]);
          setRunTotal(0);
          setRunPage(1);
          setNotice("");
        }
        setInstanceReady(true);
        return reconciled === previous;
      } catch {
        instanceRef.current = null;
        setInstanceReady(false);
        setConnection("disconnected");
        return false;
      }
    })().finally(() => { instanceCheckRef.current = null; });
    return instanceCheckRef.current;
  }, []);
  const closeOcr = useCallback(() => setOcrOpen(false), []);
  const selectedEngine = engines.find(engine => engine.id === engineId);
  const runActive = ACTIVE_STATUSES.has(activeRun?.status) || isStarting;
  const globalStatus = optimizerStatus({ connection, activeRun, starting: isStarting });
  const globalLabels = { ready: "Ready", running: "Running", connecting: "Connecting", disconnected: "Disconnected" };
  const bestResult = run?.result;
  const completedSummary = completedSearchSummary(run, activeRun);
  const bestPath = bestResult?.path || [];
  const bestPurchases = bestResult?.purchases || [];
  const continuationOptions = {
    input,
    engineId,
    engineVersion: selectedEngine?.version,
    run,
    runStatus: run?.status,
    bestPath
  };
  const continuationSeed = getContinuationSeed(continuationOptions);
  const continuationAvailable = Boolean(continuationSeed);
  const stale = Boolean(run && (run.engine_id !== engineId || !settingsMatch(input, run.input)));
  const forecastDuration = run?.input.duration_seconds || input.duration_seconds;
  const timelinePercent = Math.min(100, timelineSeconds / forecastDuration * 100);
  const remainingSeconds = Math.max(0, forecastDuration - timelineSeconds);
  const growthReady = input.unlocked_pets > 0 || input.current_levels[11] <= 0 && input.resource_counts[11] <= 0;
  const gridInput = preview && run ? run.input : input;
  const gridState = preview && timeline ? timeline : {
    levels: gridInput.current_levels,
    resources: gridInput.resource_counts,
    production_rates: null
  };
  const upgradePlan = useMemo(() => buildUpgradePlan(bestPurchases, run?.input.current_levels), [bestPurchases, run?.input.current_levels]);
  const sourceLabel = sourceInfo.kind === "ocr" ? t("Imported at {0}", [new Date(sourceInfo.at).toLocaleTimeString(localeFor(language), { hour: "2-digit", minute: "2-digit" })]) : sourceInfo.kind === "profile" ? t("Profile: {0}", [sourceInfo.name]) : sourceInfo.kind === "run" ? t("From run #{0}", [sourceInfo.id]) : sourceInfo.kind === "forecast" ? t("Forecast from run #{0}", [sourceInfo.id]) : "";
  const purchaseGroups = useMemo(() => {
    const groups = new Map();
    bestPurchases.forEach(purchase => {
      const day = Math.floor(purchase.elapsed_seconds / 86400);
      if (!groups.has(day)) groups.set(day, []);
      groups.get(day).push(purchase);
    });
    return Array.from(groups.entries()).map(([day, purchases]) => ({
      day,
      purchases
    }));
  }, [bestPurchases]);
  useEffect(() => {
    try {
      writeWorkspace(window.localStorage, {
        input,
        limits,
        engineId,
        instanceId,
        profileId,
        profileName,
        sourceInfo,
        selectedRunId,
        timelineSeconds,
        preview,
        view
      });
      setSaved(true);
    } catch {
      setSaved(false);
    }
  }, [input, limits, engineId, instanceId, profileId, profileName, sourceInfo, selectedRunId, timelineSeconds, preview, view]);
  useEffect(() => {
    request("/api/engines").then(setEngines).catch(err => setError(err.message));
  }, []);
  useEffect(() => {
    if (!instanceReady) return;
    loadProfiles(profilePage).catch(err => setError(err.message));
  }, [profilePage, instanceId, instanceReady]);
  useEffect(() => {
    if (!instanceReady) return;
    loadRuns(runPage).catch(err => setError(err.message));
  }, [runPage, instanceId, instanceReady]);

  // The running search belongs to the server, independently of the edited form.
  useEffect(() => {
    let cancelled = false;
    let timer;
    let record = null;
    async function poll() {
      let requestInstanceId = null;
      try {
        if (!await confirmInstance() || cancelled) {
          record = null;
          return;
        }
        requestInstanceId = instanceRef.current;
        const active = await request("/api/runs/active");
        if (cancelled || instanceRef.current !== requestInstanceId) return;
        setActiveRun(active[0] || null);
        if (selectedRunId) {
          if (!record || ACTIVE_STATUSES.has(record.status)) {
            const previousStatus = record?.status;
            record = active.find(item => item.id === selectedRunId) || (await request(`/api/runs/${selectedRunId}`));
            if (cancelled || instanceRef.current !== requestInstanceId) return;
            setRun(record);
            if (previousStatus !== record.status) {
              loadRuns().catch(() => {});
              if (previousStatus && !ACTIVE_STATUSES.has(record.status)) {
                setNotice(current => t(current) === t("Stopping the search and saving the best path…") ? t("Search stopped. The best path was saved.") : current);
              }
            }
          }
        } else if (active[0]) {
          setSelectedRunId(active[0].id);
        }
        setConnection("connected");
      } catch (err) {
        if (!cancelled && instanceRef.current === requestInstanceId) {
          if (err.status === 404 && selectedRunId) {
            setSelectedRunId(null);
            setRun(null);
            setPreview(false);
            setNotice(t("The selected run no longer exists. Your draft is safe."));
          } else setConnection("disconnected");
        }
      } finally {
        if (!cancelled) timer = setTimeout(poll, 1500);
      }
    }
    poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [selectedRunId, instanceId, confirmInstance, t]);
  useEffect(() => {
    let cancelled = false;
    setTimeline(null);
    if (!instanceReady || !preview || !run?.result) return;
    const requestInstanceId = instanceRef.current;
    request(`/api/runs/${run.id}/timeline?t=${timelineSeconds}`).then(value => {
      if (!cancelled && instanceRef.current === requestInstanceId) setTimeline(value);
    }).catch(err => {
      if (!cancelled && instanceRef.current === requestInstanceId) setError(err.message);
    });
    return () => {
      cancelled = true;
    };
  }, [run, preview, timelineSeconds, instanceId, instanceReady]);
  useEffect(() => {
    setTimelineSeconds(value => Math.min(value, forecastDuration));
  }, [forecastDuration]);
  async function loadProfiles(page = profilePage) {
    const requestInstanceId = instanceRef.current;
    if (!requestInstanceId) return;
    const data = await request(`/api/profiles?page=${page}&page_size=${PAGE_SIZE}`);
    if (instanceRef.current !== requestInstanceId) return;
    setProfiles(data.items);
    setProfileTotal(data.total);
  }
  async function loadRuns(page = runPage) {
    const requestInstanceId = instanceRef.current;
    if (!requestInstanceId) return;
    const data = await request(`/api/runs?page=${page}&page_size=${PAGE_SIZE}`);
    if (instanceRef.current !== requestInstanceId) return;
    setRuns(data.items);
    setRunTotal(data.total);
  }
  function editGame(next) {
    setInput(next);
    setSourceInfo({ kind: "manual" });
    setUndo(null);
  }
  function updateLevel(index, value) {
    const levels = [...input.current_levels];
    levels[index] = Math.max(0, Math.trunc(Number(value) || 0));
    editGame({
      ...input,
      current_levels: levels
    });
  }
  function updateSpeed(index, value) {
    const levels = [...input.current_levels];
    levels[index + 12] = Math.min(10, Math.max(0, Math.trunc(Number(value) || 0)));
    editGame({
      ...input,
      current_levels: levels
    });
  }
  function updateCount(index, value) {
    const counts = [...input.resource_counts];
    counts[index] = Math.max(0, Number(value) || 0);
    editGame({
      ...input,
      resource_counts: counts
    });
  }
  function applyOcrState(parsed) {
    const levels = [...input.current_levels];
    parsed.levels.forEach((value, index) => {
      levels[index] = value;
    });
    parsed.speeds.forEach((value, index) => {
      levels[index + 12] = value;
    });
    editGame({
      ...input,
      current_levels: levels,
      resource_counts: parsed.counts,
      starting_path: [],
      ...(parsed.duration_seconds != null ? { duration_seconds: parsed.duration_seconds } : {})
    });
    setSourceInfo({ kind: "ocr", at: Date.now() });
    setPreview(false);
    setOcrOpen(false);
    setNotice(t("Game state updated. The previous result is still available."));
  }
  async function openRun(savedRun) {
    if (!instanceReady || !await confirmInstance()) return;
    setRun(savedRun);
    setSelectedRunId(savedRun.id);
    setTimeline(null);
    setTimelineSeconds(0);
    setPreview(true);
    setView("dashboard");
  }
  async function loadRunSettings(savedRun) {
    if (!instanceReady || !await confirmInstance()) return;
    setInput({ ...structuredClone(savedRun.input), starting_path: [] });
    setLimits(normalizeSearchLimits({
      ...DEFAULT_LIMITS,
      ...savedRun.limits
    }));
    setEngineId(savedRun.engine_id);
    setSourceInfo({ kind: "run", id: savedRun.id });
    setPreview(false);
    setUndo(null);
    setNotice(t("Settings from run #{0} loaded. Check this saved snapshot against the game.", [savedRun.id]));
  }
  async function startRun() {
    if (!instanceReady || startGuard.current || runActive) return;
    if (input.duration_seconds <= 0) { setError(t("Event time must be greater than zero.")); return; }
    if (!growthReady) {
      setError(t("Enter your unlocked pet count to calculate Growth."));
      return;
    }
    startGuard.current = true;
    setIsStarting(true);
    setError("");
    setNotice("");
    try {
      if (!await confirmInstance()) return;
      const requestInstanceId = instanceRef.current;
      const created = await request("/api/runs", {
        method: "POST",
        body: JSON.stringify(buildRunRequest(continuationOptions, normalizeSearchLimits(limits)))
      });
      if (instanceRef.current !== requestInstanceId) return;
      if (continuationSeed) {
        setInput(current => current.starting_path === input.starting_path
          ? { ...current, starting_path: [] } : current);
      }
      setRun(created);
      setSelectedRunId(created.id);
      setActiveRun(ACTIVE_STATUSES.has(created.status) ? created : null);
      setTimeline(null);
      setTimelineSeconds(0);
      setPreview(false);
      setRunPage(1);
      loadRuns(1).catch(() => {});
    } catch (err) {
      setError(err.message);
    } finally {
      startGuard.current = false;
      setIsStarting(false);
    }
  }
  async function stopRun() {
    if (!instanceReady || !activeRun) return;
    try {
      if (!await confirmInstance()) return;
      const requestInstanceId = instanceRef.current;
      await request(`/api/runs/${activeRun.id}/stop`, {
        method: "POST"
      });
      if (instanceRef.current !== requestInstanceId) return;
      setNotice(t("Stopping the search and saving the best path…"));
    } catch (err) {
      setError(err.message);
    }
  }
  async function useTimelineAsStart() {
    if (!instanceReady || !timeline || !run || !await confirmInstance()) return;
    setUndo({
      input: structuredClone(input),
      engineId,
      sourceInfo
    });
    setInput(inputAtTimeline(run.input, timeline));
    setEngineId(run.engine_id);
    setSourceInfo({ kind: "forecast", id: run.id });
    setPreview(false);
    setNotice(t("Forecast adopted as the current state. Time and busy periods were shifted."));
  }
  async function undoTimelineStart() {
    if (!instanceReady || !undo || !await confirmInstance()) return;
    setInput(undo.input);
    setEngineId(undo.engineId);
    setSourceInfo(undo.sourceInfo);
    setUndo(null);
    setNotice(t("Original game state restored."));
  }
  function updateBusyTime(index, key, value) {
    setInput({
      ...input,
      busy_times: input.busy_times.map((busy, i) => i === index ? {
        ...busy,
        [key]: Number(value)
      } : busy)
    });
  }
  async function saveProfile(copy = false) {
    if (!instanceReady || isSavingProfile) return;
    setIsSavingProfile(true);
    setError("");
    try {
      if (!await confirmInstance()) return;
      const requestInstanceId = instanceRef.current;
      const updating = profileId && !copy;
      const profile = await request(updating ? `/api/profiles/${profileId}` : "/api/profiles", {
        method: updating ? "PUT" : "POST",
        body: JSON.stringify({
          name: profileName,
          engine_id: engineId,
          input,
          limits: normalizeSearchLimits(limits)
        })
      });
      if (instanceRef.current !== requestInstanceId) return;
      setProfileId(profile.id);
      setProfilePage(1);
      await loadProfiles(1);
      if (instanceRef.current !== requestInstanceId) return;
      setNotice(t("Profile \"{0}\" saved, including search limits.", [profileName]));
    } catch (err) {
      setError(err.message);
    } finally {
      setIsSavingProfile(false);
    }
  }
  async function loadProfile(profile) {
    if (!instanceReady || !await confirmInstance()) return;
    setInput({ ...structuredClone(profile.input), duration_seconds: input.duration_seconds });
    setLimits(normalizeSearchLimits({
      ...DEFAULT_LIMITS,
      ...profile.limits
    }));
    setEngineId(profile.engine_id);
    setProfileId(profile.id);
    setProfileName(profile.name);
    setSourceInfo({ kind: "profile", name: profile.name });
    setPreview(false);
    setUndo(null);
    setNotice(t("Profile \"{0}\" loaded. The previous result is still available.", [profile.name]));
  }
  async function deleteProfile(id) {
    if (!instanceReady) return;
    try {
      if (!await confirmInstance()) return;
      const requestInstanceId = instanceRef.current;
      await request(`/api/profiles/${id}`, {
        method: "DELETE"
      });
      if (instanceRef.current !== requestInstanceId) return;
      if (profileId === id) setProfileId(null);
      const page = profiles.length === 1 ? Math.max(1, profilePage - 1) : profilePage;
      setProfilePage(page);
      await loadProfiles(page);
    } catch (err) {
      setError(err.message);
    }
  }
  async function deleteRun(savedRun) {
    if (!instanceReady) return;
    try {
      if (!await confirmInstance()) return;
      const requestInstanceId = instanceRef.current;
      await request(`/api/runs/${savedRun.id}`, {
        method: "DELETE"
      });
      if (instanceRef.current !== requestInstanceId) return;
      if (selectedRunId === savedRun.id) {
        setSelectedRunId(null);
        setRun(null);
        setTimeline(null);
        setPreview(false);
      }
      const page = runs.length === 1 ? Math.max(1, runPage - 1) : runPage;
      setRunPage(page);
      await loadRuns(page);
    } catch (err) {
      setError(err.message);
    }
  }
  function applyStartingPathCsv(value) {
    if (!value.trim()) {
      setInput({
        ...input,
        starting_path: []
      });
      return;
    }
    const path = value.split(",").map(item => item.trim()).filter(Boolean).map(Number);
    if (path.some(item => !Number.isInteger(item) || item < 0 || item > 24)) {
      setError(t("The path must contain whole numbers from 0 to 24."));
      return;
    }
    setInput({
      ...input,
      starting_path: path
    });
    setError("");
  }
  const navigation = [["dashboard", t("Planner"), "01"], ["runs", t("History"), "02"], ["settings", t("Settings"), "03"]];
  return <main className="app-shell">
      <aside className="sidebar">
        <div className="brand"><div className="brand-mark">IT</div><div><h1>ITRTG</h1><p>Halloween 2026</p></div></div>
        <nav aria-label={t("Main navigation")}>{navigation.map(([key, label, index]) => <button className={view === key ? "active" : ""} key={key} onClick={() => setView(key)}><span>{index}</span>{label}</button>)}</nav>
        <div className="status-block"><span className={`status-dot ${globalStatus}`} /><div><small>{t("Optimizer")}</small><strong>{t(globalLabels[globalStatus])}</strong></div></div>
        <label className="language-picker">{t("Language")}<select aria-label={t("Language")} value={language} onChange={event => setLanguage(event.target.value)}><option value="en">English</option><option value="ru">Русский</option></select></label><small className="save-status" role="status">{saved ? t("Saved in this browser") : t("Could not save the draft")}</small><details className="event-note sidebar-event-note"><summary>{t("About event data")}</summary><p>{t("Costs match the Discord report. Ingredients and production times are assumed from the summer event.")}</p></details>
      </aside>
      <section className="workspace">
        {connection === "disconnected" && <div className="banner danger">{t("Cannot reach the backend. Reconnecting automatically.")}</div>}
        {error && <div className="banner danger"><span>{t(error)}</span><button onClick={() => setError("")} aria-label={t("Close")}>×</button></div>}
        {notice && <div className="banner success"><span>{t(notice)}</span><div className="actions">{undo && <button className="button ghost" onClick={undoTimelineStart}>{t("Undo adoption")}</button>}<button onClick={() => setNotice("")} aria-label={t("Close")}>×</button></div></div>}
        {activeRun && <ActiveSearch key={activeRun.id} run={activeRun} connection={connection} onShow={() => openRun(activeRun)} onStop={stopRun} />}

        {view === "dashboard" && <>
          <section className="page-header"><h2>{t("Planner")}</h2><button className="button secondary" onClick={() => setOcrOpen(true)}>{t("Import screenshot")}</button></section>
          {completedSummary && <CompletedSearch run={run} summary={completedSummary} />}
          <section className="section-card planner-controls"><DurationInputs value={input.duration_seconds} label={t("Time until event ends")} onChange={(value) => setInput({ ...input, duration_seconds: value })} /><label className="search-budget">{t("Search time, seconds")}<input type="number" min="0.1" max="3600" value={limits.time_limit_seconds} onChange={(event) => setLimits({ ...limits, time_limit_seconds: Number(event.target.value) })} /></label><button className="button" disabled={!instanceReady || runActive || !growthReady || input.duration_seconds <= 0 || connection !== "connected"} onClick={startRun}>{isStarting ? t("Starting…") : continuationSeed?.mode === "trimmed" ? t("Improve remaining plan") : continuationAvailable ? t("Continue search") : t("Calculate plan")}</button>{input.duration_seconds <= 0 && <small className="field-error">{t("Event time must be greater than zero.")}</small>}</section>
          {!growthReady && <div className="readiness-warning"><span>{t("Enter your unlocked pet count for Growth.")}</span><button className="button ghost" onClick={() => setView("settings")}>{t("Settings")}</button></div>}
          {stale && <div className="readiness-warning">{continuationSeed?.mode === "trimmed" ? <span>{t("Starting from run #{0}: {1} completed purchases skipped.", [run.id, continuationSeed.removedPurchases])}</span> : <><span>{t("Inputs changed. Run #")}{run.id} {t("is still available for the previous settings.")}</span><button className="button ghost" onClick={() => loadRunSettings(run)}>{t("Load its settings")}</button></>}</div>}
          {run?.error && <div className="banner danger">{t(run.error)}</div>}
          {run?.status === "interrupted" && !continuationAvailable && <div className="readiness-warning">{t("The search was interrupted.")} {run.result ? t("Load its settings to continue from the saved best path.") : t("No path was saved yet. Load its settings and start a new search.")}</div>}
          {bestResult && <section className="section-card path-panel"><div className="section-heading"><h3>{!stale && !preview && ["manual", "ocr"].includes(sourceInfo.kind) ? t("Available now") : t("At the start of this plan")}</h3></div>{upgradePlan.now ? <UpgradeBatch batch={upgradePlan.now} /> : <p className="empty-state compact">{t("No upgrades at the start of this plan.")}</p>}
            {upgradePlan.next.length > 0 && <><h3>{t("Next steps")}</h3><ol className="next-upgrades">{upgradePlan.next.map((batch) => <li key={batch.elapsed_seconds}><strong className="upgrade-time">{t("After {0} from plan start", [formatTime(batch.elapsed_seconds, language)])}</strong><UpgradeBatch batch={batch} /></li>)}</ol></>}
            {instanceReady && <FullPlan key={`${instanceId}:${run.id}`} instanceId={instanceId} run={run} groups={purchaseGroups} purchases={bestPurchases} />}
          </section>}
          <section className="section-card state-section">
            <div className="section-heading"><h3>{preview ? t("Forecast #{0} at {1}", [run?.id || "…", formatTime(timelineSeconds, language)]) : t("Game state")}</h3>{!preview && sourceLabel && <small className="source-label" title={sourceInfo.at ? new Date(sourceInfo.at).toLocaleString(localeFor(language)) : undefined}>{sourceLabel}</small>}<div className="actions"><button className={`button ${!preview ? "secondary" : "ghost"}`} onClick={() => setPreview(false)}>{t("Edit")}</button><button className={`button ${preview ? "secondary" : "ghost"}`} disabled={!run?.result} onClick={() => setPreview(true)}>{t("Forecast")}</button></div></div>
            {preview && !timeline && <p role="status">{t("Loading forecast…")}</p>}
            <div className="resource-grid">{resourceLabels.map((label, index) => <article className="resource-card" key={label}><header><span>{String(index + 1).padStart(2, "0")}</span><strong>{label}</strong>{gridState.production_rates && <small>{formatNumber(gridState.production_rates[index], 2, language)}{t("/s")}</small>}</header><div className="resource-fields"><label>Level<input aria-label={`${label} Level`} type="number" min="0" value={gridState.levels[index] ?? 0} onChange={event => updateLevel(index, event.target.value)} disabled={preview} /></label><label>Speed<input aria-label={`${label} Speed`} type="number" min="0" max="10" value={gridState.levels[index + 12] ?? 0} onChange={event => updateSpeed(index, event.target.value)} disabled={preview} /></label><label>Count<input aria-label={`${label} Count`} type="number" min="0" value={Math.round(gridState.resources[index] ?? 0)} onChange={event => updateCount(index, event.target.value)} disabled={preview} /></label></div></article>)}</div>
          </section>
          {run && <section className="section-card timeline-panel">
            <div className="section-heading"><h3>{t("Run #")}{run.id} · {t(statusLabels[run.status])}</h3><span>{t("Horizon")} {formatTime(forecastDuration, language)}</span></div>
            {bestResult && <div className="reward-grid">{[10, 6, 8, 9, 11].map(index => <div key={index}><span>{resourceLabels[index]}</span><strong>{formatNumber(bestResult.final_resources[index], 0, language)}</strong></div>)}</div>}
            <div className="timeline-control"><input aria-label={t("Timeline time")} type="range" min="0" max={forecastDuration} value={timelineSeconds} disabled={!bestResult} onChange={event => {
              setPreview(true);
              setTimelineSeconds(Number(event.target.value));
            }} /><div className="timeline-ticks"><span>{t("Start")}</span><span>{formatTime(timelineSeconds, language)}</span><span>{formatTime(forecastDuration, language)}</span></div></div>
            <div className="actions"><button className="button secondary" disabled={!preview || !timeline || remainingSeconds <= 0} onClick={useTimelineAsStart}>{t("Adopt forecast as current state")}</button><button className="button ghost" onClick={() => loadRunSettings(run)}>{t("Load run settings")}</button></div>
            <details className="technical-details"><summary>{t("Run details")}</summary><RunSettingsSummary savedRun={run} /><p>{t("Optimization points")}: {formatScore(bestResult?.score, localeFor(language))} {t("· Purchases:")} {bestPurchases.length}</p><button className="button ghost" disabled={!bestPath.length} onClick={() => navigator.clipboard.writeText(bestPath.join(",")).then(() => setNotice(t("Path CSV copied."))).catch(err => setError(err.message))}>{t("Copy CSV")}</button></details>
          </section>}

        </>}

        {view === "runs" && <section className="section-card runs-page"><div className="page-header compact"><h2>{t("History")}</h2><button className="button ghost" onClick={() => loadRuns().catch(err => setError(err.message))}>{t("Refresh")}</button></div>{!runs.length ? <div className="empty-state compact">{t("No saved runs yet.")}</div> : <div className="run-history">{runs.map(item => <article className="history-row" key={item.id}>
          <div className="history-main"><strong>{item.created_at ? new Date(item.created_at).toLocaleString(localeFor(language), { dateStyle: "medium", timeStyle: "short" }) : t("Run #{0}", [item.id])}</strong><span className="run-status">{t(statusLabels[item.status])}</span><small>{t("Forecast period: {0}", [formatTime(item.input.duration_seconds, language)])}</small></div>
          <div className="history-score"><span>{t("Optimization points")}</span><strong>{formatScore(item.score ?? item.result?.score, localeFor(language))}</strong></div>
          <div className="history-actions"><button className="button secondary" onClick={() => openRun(item)}>{t("Open plan")}</button><details className="history-menu"><summary>{t("More")}</summary><div className="history-menu-items"><button className="button ghost" onClick={() => { openRun(item); loadRunSettings(item); }}>{t("Load to continue")}</button><button className="button danger subtle" disabled={ACTIVE_STATUSES.has(item.status)} onClick={() => deleteRun(item)}>{t("Delete")}</button></div></details></div>
        </article>)}</div>}<Pagination page={runPage} total={runTotal} pageSize={PAGE_SIZE} onPageChange={setRunPage} /></section>}

        {view === "settings" && <section className="settings-page"><div className="page-header"><h2>{t("Settings")}</h2><button className="button" disabled={!instanceReady || isSavingProfile} onClick={() => saveProfile(false)}>{profileId ? t("Save profile changes") : t("Save profile")}</button></div><div className="settings-layout">
          <section className="section-card settings-section"><h3>{t("Game")}</h3><div className="form-grid three">{[["unlocked_pets", t("Unlocked pets")], ["dungeon_levels", t("Dungeon levels")], ["adventure_level", t("Adventure level")]].map(([key, label]) => <label key={key}>{label}<input type="number" min="0" value={input[key]} onChange={event => setInput({
                  ...input,
                  [key]: Math.max(0, Math.trunc(Number(event.target.value) || 0))
                })} /></label>)}</div></section>
          <section className="section-card settings-section"><div className="section-heading"><h3>{t("Busy periods")}</h3><button className="button ghost" onClick={() => setInput({
                ...input,
                busy_times: [...input.busy_times, {
                  start_hour: 0,
                  end_hour: 1
                }]
              })}>{t("Add")}</button></div><small>{t("Hours from the start of the simulation")}</small>{input.busy_times.map((busy, index) => <div className="busy-row" key={index}><label>{t("From")}<input type="number" min="0" value={busy.start_hour} onChange={event => updateBusyTime(index, "start_hour", event.target.value)} /></label><label>{t("To")}<input type="number" min="0" value={busy.end_hour} onChange={event => updateBusyTime(index, "end_hour", event.target.value)} /></label><button className="button ghost" onClick={() => setInput({
                ...input,
                busy_times: input.busy_times.filter((_, i) => i !== index)
              })}>{t("Delete")}</button></div>)}</section>
          <details className="section-card settings-section advanced-settings"><summary>{t("Advanced search settings")}</summary><div className="weights-grid">{weightKeys.map(key => <label key={key}>{weightLabels[key]}<input type="number" step="0.000001" value={input.weights[key]} onChange={event => setInput({
                  ...input,
                  weights: {
                    ...input.weights,
                    [key]: Number(event.target.value)
                  }
                })} /></label>)}</div><label>{t("Starting path CSV")}<input key={JSON.stringify(input.starting_path)} defaultValue={input.starting_path.join(",")} onBlur={event => applyStartingPathCsv(event.target.value)} /></label>{engines.length > 1 && <label>{t("Engine")}<select value={engineId} onChange={event => setEngineId(event.target.value)}>{engines.map(engine => <option key={engine.id} value={engine.id}>{engine.name}</option>)}</select></label>}</details>
          <section className="section-card profiles-section"><div className="section-heading"><h3>{t("Profiles")}</h3>{profileId && <button className="button ghost" disabled={!instanceReady || isSavingProfile} onClick={() => saveProfile(true)}>{t("Save a copy")}</button>}</div><label>{t("Name")}<input value={profileName} onChange={event => setProfileName(event.target.value)} /></label><div className="profiles">{profiles.map(profile => <article className="profile-card" key={profile.id}><div><strong>{profile.name}{profile.id === profileId ? t(" · selected") : ""}</strong></div><div className="profile-actions"><button className="button secondary" onClick={() => loadProfile(profile)}>{t("Load")}</button><button className="button danger subtle" onClick={() => deleteProfile(profile.id)}>{t("Delete")}</button></div></article>)}</div><Pagination page={profilePage} total={profileTotal} pageSize={PAGE_SIZE} onPageChange={setProfilePage} /></section>
        </div></section>}
      </section>
      {ocrOpen && <OcrImportDialog onClose={closeOcr} onApply={applyOcrState} />}
    </main>;
}
