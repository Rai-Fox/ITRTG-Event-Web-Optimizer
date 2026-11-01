from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import time
from pathlib import Path
from threading import Event, Thread, Lock
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from itrtg_web.engines import build_registry
from itrtg_web.continuation import ContinuedSearch, continuation_path
from itrtg_web.frontend import mount_frontend
from itrtg_web.models import EngineMetadata, OcrParseResult, OptimizerResult, Profile, ProfileIn, ProfilePage, RunCreate, RunPage, RunRecord, SimulateRequest, TimelineSnapshot
from itrtg_web.ocr import OcrEngineId, available_ocr_engines, parse_image
from itrtg_web.storage import create_profile, create_run, delete_profile, delete_run, get_profile, get_run, init_db, list_profiles, list_runs, update_profile, update_run
from itrtg_web.storage import active_runs, get_instance_id, recover_interrupted_runs

app = FastAPI(title="ITRTG Web Optimizer")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080", "http://127.0.0.1:8080"],
    allow_methods=["*"],
    allow_headers=["*"],
)

registry = build_registry()
run_stop_events: dict[int, Event] = {}
run_progress: dict[int, dict[str, Any]] = {}
run_latest_results: dict[int, OptimizerResult] = {}
run_threads: dict[int, Thread] = {}
run_start_lock = Lock()
shutting_down = False
logger = logging.getLogger(__name__)


@app.on_event("startup")
def startup() -> None:
    global shutting_down
    shutting_down = False
    init_db()
    recover_interrupted_runs()


def stop_workers() -> None:
    global shutting_down
    with run_start_lock:
        shutting_down = True
        for event in list(run_stop_events.values()):
            event.set()
        threads = list(run_threads.values())
    deadline = time.monotonic() + 10
    for thread in threads:
        thread.join(timeout=max(0, deadline - time.monotonic()))


@app.on_event("shutdown")
def shutdown() -> None:
    stop_workers()


@app.post("/api/shutdown")
def prepare_shutdown() -> dict:
    stop_workers()
    if any(thread.is_alive() for thread in list(run_threads.values())):
        raise HTTPException(status_code=409, detail="The search is still stopping")
    return {"status": "stopped"}


def progress_payload(result: OptimizerResult, status: str = "running") -> dict[str, Any]:
    return {
        "status": status,
        "score": result.score,
        "search_stats": result.search_stats.model_dump() if result.search_stats else None,
        "pathLength": len(result.path),
        "purchaseCount": len(result.purchases),
        "path": result.path,
        "purchases": [purchase.model_dump() for purchase in result.purchases],
    }


def with_live_result(run: RunRecord) -> RunRecord:
    result = run_latest_results.get(run.id)
    if run.status in {"queued", "running"} and result is not None:
        return run.model_copy(update={"result": result, "score": result.score})
    return run


@app.get("/api/engines", response_model=list[EngineMetadata])
def engines() -> list[EngineMetadata]:
    return [engine.metadata() for engine in registry.list()]


@app.get("/api/instance")
def instance() -> dict[str, str]:
    return {"id": get_instance_id()}


@app.get("/api/engines/{engine_id}/schema")
def engine_schema(engine_id: str) -> dict:
    try:
        return registry.get(engine_id).schema()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/engines/{engine_id}/simulate", response_model=OptimizerResult)
def engine_simulate(engine_id: str, payload: SimulateRequest) -> OptimizerResult:
    try:
        engine = registry.get(engine_id)
        engine.validate(payload.input)
        return engine.simulate(payload.input, payload.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/profiles", response_model=ProfilePage)
def profiles(page: int = Query(default=1, ge=1), page_size: int = Query(default=10, ge=1, le=100)) -> ProfilePage:
    items, total = list_profiles(page, page_size)
    return ProfilePage(items=items, total=total, page=page, page_size=page_size)


@app.post("/api/profiles", response_model=Profile)
def create_profile_endpoint(profile: ProfileIn) -> Profile:
    try:
        engine = registry.get(profile.engine_id)
        engine.validate(profile.input)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return create_profile(profile, engine.metadata().version)


@app.put("/api/profiles/{profile_id}", response_model=Profile)
def update_profile_endpoint(profile_id: int, profile: ProfileIn) -> Profile:
    try:
        engine = registry.get(profile.engine_id)
        engine.validate(profile.input)
        return update_profile(profile_id, profile, engine.metadata().version)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/profiles/{profile_id}", status_code=204)
def delete_profile_endpoint(profile_id: int) -> None:
    try:
        delete_profile(profile_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/ocr/engines")
def ocr_engines() -> list[dict[str, str]]:
    return available_ocr_engines()


@app.post("/api/ocr/parse", response_model=OcrParseResult)
async def ocr_parse(
    file: UploadFile = File(...), engine: OcrEngineId = Form("tesseract"),
) -> OcrParseResult:
    if engine not in {item["id"] for item in available_ocr_engines()}:
        raise HTTPException(status_code=400, detail="Windows OCR is only available on Windows.")
    suffix = Path(file.filename or "upload.png").suffix or ".png"
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="The image file is empty.")
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(contents)
        tmp_path = Path(tmp.name)
    try:
        return await parse_image(tmp_path, engine)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    finally:
        tmp_path.unlink(missing_ok=True)


@app.post("/api/runs", response_model=RunRecord)
def start_run(payload: RunCreate) -> RunRecord:
    with run_start_lock:
        if shutting_down:
            raise HTTPException(status_code=409, detail="The application is shutting down")
        if active_runs():
            raise HTTPException(status_code=409, detail="A search is already running. Stop it before starting another.")
        return _start_run(payload)


def _start_run(payload: RunCreate) -> RunRecord:
    try:
        engine = registry.get(payload.engine_id)
        engine.validate(payload.input)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.profile_id is not None:
        try:
            get_profile(payload.profile_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    input_data = payload.input
    continuation = None
    initial = None
    if payload.parent_run_id is not None:
        try:
            parent = get_run(payload.parent_run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="The saved run to continue was not found.") from exc
        if parent.status not in {"completed", "stopped", "interrupted"} or parent.result is None:
            raise HTTPException(status_code=409, detail="Only a finished saved run with a result can be continued.")
        if (parent.engine_id != payload.engine_id or parent.engine_version != engine.metadata().version
                or parent.result.engine_id != parent.engine_id
                or parent.result.engine_version != parent.engine_version):
            raise HTTPException(status_code=409, detail="The saved run uses an incompatible optimization engine.")
        try:
            path = continuation_path(parent, input_data)
            if input_data.starting_path and input_data.starting_path != path:
                raise ValueError("The starting path does not match the saved plan.")
            input_data = input_data.model_copy(update={"starting_path": path})
            initial = engine.simulate(input_data, path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        continuation = ContinuedSearch(parent, initial, input_data)
        initial = continuation.merge(initial)

    run = create_run(payload.profile_id, payload.engine_id, engine.metadata().version, input_data, payload.limits, payload.parent_run_id)
    if initial is not None:
        run = update_run(run.id, "queued", result=initial, score=initial.score)
        run_latest_results[run.id] = initial
    stop_event = Event()
    run_stop_events[run.id] = stop_event
    run_progress[run.id] = progress_payload(initial, "queued") if initial is not None else {"status": "queued", "score": None}

    last_checkpoint = 0.0

    def progress(result: OptimizerResult) -> None:
        nonlocal last_checkpoint
        if continuation is not None:
            result = continuation.merge(result)
        run_latest_results[run.id] = result.model_copy()
        run_progress[run.id] = progress_payload(result)
        now = time.monotonic()
        if now - last_checkpoint >= 1:
            update_run(run.id, "running", result=result, score=result.score)
            last_checkpoint = now

    def worker() -> None:
        try:
            update_run(run.id, "running")
            run_progress[run.id] = progress_payload(initial) if initial is not None else {"status": "running", "score": None}
            result = engine.optimize(input_data, payload.limits, stop_event=stop_event, progress=progress)
            if continuation is not None:
                result = continuation.merge(result)
            status = "stopped" if result.stopped or stop_event.is_set() else "completed"
            update_run(run.id, status, result=result, score=result.score)
            run_progress[run.id] = progress_payload(result, status)
        except Exception as exc:
            logger.exception("Optimizer run %s failed", run.id)
            latest = run_latest_results.get(run.id)
            update_run(run.id, "failed", result=latest, score=latest.score if latest else None, error=str(exc))
            run_progress[run.id] = {"status": "failed", "error": str(exc)}
        finally:
            run_latest_results.pop(run.id, None)
            run_stop_events.pop(run.id, None)
            run_threads.pop(run.id, None)

    thread = Thread(target=worker, daemon=True)
    run_threads[run.id] = thread
    thread.start()
    return with_live_result(get_run(run.id))


@app.get("/api/runs", response_model=RunPage)
def runs(page: int = Query(default=1, ge=1), page_size: int = Query(default=10, ge=1, le=100)) -> RunPage:
    items, total = list_runs(page, page_size)
    return RunPage(items=[with_live_result(item) for item in items], total=total, page=page, page_size=page_size)


@app.get("/api/runs/active", response_model=list[RunRecord])
def running_runs() -> list[RunRecord]:
    return [with_live_result(run) for run in active_runs()]


@app.post("/api/runs/{run_id}/stop", response_model=RunRecord)
def stop_run(run_id: int) -> RunRecord:
    event = run_stop_events.get(run_id)
    if not event:
        try:
            return get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    event.set()
    return with_live_result(get_run(run_id))


@app.get("/api/runs/{run_id}", response_model=RunRecord)
def run_detail(run_id: int) -> RunRecord:
    try:
        return with_live_result(get_run(run_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/api/runs/{run_id}", status_code=204)
def delete_run_endpoint(run_id: int) -> None:
    try:
        run = get_run(run_id)
        if run.status in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="Stop the run before deleting it")
        delete_run(run_id)
        run_progress.pop(run_id, None)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}/timeline", response_model=TimelineSnapshot)
def run_timeline(run_id: int, t: float) -> TimelineSnapshot:
    try:
        run = get_run(run_id)
        if run.result is None:
            raise HTTPException(status_code=409, detail="Run has no result yet")
        if run.engine_version != run.result.engine_version:
            raise HTTPException(status_code=409, detail="Run result engine version is incompatible")
        return registry.get(run.engine_id).timeline(run.result, t)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: int) -> StreamingResponse:
    async def stream():
        while True:
            try:
                record = get_run(run_id)
            except KeyError:
                yield f"data: {json.dumps({'status': 'missing'})}\n\n"
                break
            payload = run_progress.get(run_id, {"status": record.status, "score": record.score})
            yield f"data: {json.dumps(payload)}\n\n"
            if record.status in {"completed", "stopped", "failed", "interrupted"}:
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream")


mount_frontend(app, Path(__file__).resolve().parents[2] / "frontend" / "dist")
