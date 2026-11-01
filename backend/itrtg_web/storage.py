from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from itrtg_web.models import OptimizerInput, OptimizerResult, Profile, ProfileIn, RunLimits, RunRecord

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "itrtg.sqlite3"


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                engine_id TEXT NOT NULL,
                engine_version TEXT NOT NULL,
                input_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER,
                engine_id TEXT NOT NULL,
                engine_version TEXT NOT NULL,
                status TEXT NOT NULL,
                score REAL,
                input_json TEXT NOT NULL,
                limits_json TEXT,
                result_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
        if "limits_json" not in columns:
            conn.execute("ALTER TABLE runs ADD COLUMN limits_json TEXT")
        profile_columns = {row["name"] for row in conn.execute("PRAGMA table_info(profiles)").fetchall()}
        if "limits_json" not in profile_columns:
            conn.execute("ALTER TABLE profiles ADD COLUMN limits_json TEXT")


def recover_interrupted_runs() -> None:
    with connect() as conn:
        conn.execute("UPDATE runs SET status = 'interrupted', updated_at = CURRENT_TIMESTAMP WHERE status IN ('queued', 'running')")


def active_runs() -> list[RunRecord]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM runs WHERE status IN ('queued', 'running') ORDER BY id DESC").fetchall()
    return [_run_from_row(row) for row in rows]


def _loads(value: str) -> dict[str, Any]:
    return json.loads(value)


def _dumps(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    return json.dumps(value, separators=(",", ":"))


def create_profile(profile: ProfileIn, engine_version: str) -> Profile:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO profiles (name, engine_id, engine_version, input_json, limits_json) VALUES (?, ?, ?, ?, ?)",
            (profile.name, profile.engine_id, engine_version, _dumps(profile.input), _dumps(profile.limits) if profile.limits else None),
        )
        profile_id = int(cur.lastrowid)
    return get_profile(profile_id)


def list_profiles(page: int = 1, page_size: int = 10) -> tuple[list[Profile], int]:
    offset = (page - 1) * page_size
    with connect() as conn:
        total = int(conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0])
        rows = conn.execute("SELECT * FROM profiles ORDER BY id DESC LIMIT ? OFFSET ?", (page_size, offset)).fetchall()
    return [_profile_from_row(row) for row in rows], total


def get_profile(profile_id: int) -> Profile:
    with connect() as conn:
        row = conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
    if row is None:
        raise KeyError(f"Profile {profile_id} not found")
    return _profile_from_row(row)


def update_profile(profile_id: int, profile: ProfileIn, engine_version: str) -> Profile:
    with connect() as conn:
        cur = conn.execute(
            """
            UPDATE profiles
            SET name = ?, engine_id = ?, engine_version = ?, input_json = ?, limits_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (profile.name, profile.engine_id, engine_version, _dumps(profile.input), _dumps(profile.limits) if profile.limits else None, profile_id),
        )
        if cur.rowcount == 0:
            raise KeyError(f"Profile {profile_id} not found")
    return get_profile(profile_id)


def delete_profile(profile_id: int) -> None:
    with connect() as conn:
        cur = conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        if cur.rowcount == 0:
            raise KeyError(f"Profile {profile_id} not found")


def create_run(profile_id: int | None, engine_id: str, engine_version: str, input_data: OptimizerInput, limits: RunLimits) -> RunRecord:
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO runs (profile_id, engine_id, engine_version, status, input_json, limits_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (profile_id, engine_id, engine_version, "queued", _dumps(input_data), _dumps(limits)),
        )
        run_id = int(cur.lastrowid)
    return get_run(run_id)


def update_run(
    run_id: int,
    status: str,
    result: OptimizerResult | None = None,
    score: float | None = None,
    error: str | None = None,
) -> RunRecord:
    result_json = _dumps(result) if result else None
    with connect() as conn:
        cur = conn.execute(
            """
            UPDATE runs
            SET status = ?, score = COALESCE(?, score), result_json = COALESCE(?, result_json), error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, score, result_json, error, run_id),
        )
        if cur.rowcount == 0:
            raise KeyError(f"Run {run_id} not found")
    return get_run(run_id)


def list_runs(page: int = 1, page_size: int = 10) -> tuple[list[RunRecord], int]:
    offset = (page - 1) * page_size
    with connect() as conn:
        total = int(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0])
        rows = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ? OFFSET ?", (page_size, offset)).fetchall()
    return [_run_from_row(row) for row in rows], total


def delete_run(run_id: int) -> None:
    with connect() as conn:
        cur = conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        if cur.rowcount == 0:
            raise KeyError(f"Run {run_id} not found")


def get_run(run_id: int) -> RunRecord:
    with connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise KeyError(f"Run {run_id} not found")
    return _run_from_row(row)


def _run_from_row(row: sqlite3.Row) -> RunRecord:
    result = OptimizerResult.model_validate(_loads(row["result_json"])) if row["result_json"] else None
    return RunRecord(
        id=row["id"],
        profile_id=row["profile_id"],
        engine_id=row["engine_id"],
        engine_version=row["engine_version"],
        status=row["status"],
        score=row["score"],
        input=OptimizerInput.model_validate(_loads(row["input_json"])),
        limits=RunLimits.model_validate(_loads(row["limits_json"])) if row["limits_json"] else None,
        result=result,
        error=row["error"],
        created_at=_utc_timestamp(row["created_at"]),
        updated_at=_utc_timestamp(row["updated_at"]),
    )


def _utc_timestamp(value: str | None) -> datetime | None:
    """SQLite CURRENT_TIMESTAMP is UTC but does not include an offset."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _profile_from_row(row: sqlite3.Row) -> Profile:
    return Profile(
        id=row["id"],
        name=row["name"],
        engine_id=row["engine_id"],
        engine_version=row["engine_version"],
        input=OptimizerInput.model_validate(_loads(row["input_json"])),
        limits=RunLimits.model_validate(_loads(row["limits_json"])) if row["limits_json"] else None,
    )
