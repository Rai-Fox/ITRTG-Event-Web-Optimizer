from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


RESOURCE_KEYS = [
    "grave_stone",
    "spider_web",
    "cup",
    "moon",
    "candle",
    "broom",
    "free_exp",
    "skull",
    "pet_stones",
    "research_points",
    "event_currency",
    "growth",
]

RESOURCE_LABELS = [
    "Grave Stone",
    "Spider Web",
    "Cup",
    "Moon",
    "Candle",
    "Broom",
    "Free Exp",
    "Skull",
    "Pet Stones",
    "Research Points",
    "Witch Hat",
    "Growth",
]


class EngineMetadata(BaseModel):
    id: str
    name: str
    version: str
    capabilities: list[str]
    resource_labels: list[str] = Field(default_factory=lambda: RESOURCE_LABELS.copy())
    config_schema: dict[str, Any]


class BusyTime(BaseModel):
    start_hour: float = Field(ge=0)
    end_hour: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_interval(self):
        if self.end_hour <= self.start_hour:
            raise ValueError("A busy period must end after it starts")
        return self


class RunLimits(BaseModel):
    time_limit_seconds: float = Field(default=30.0, ge=0.1, le=3600)
    max_iterations: int | None = Field(default=None, ge=1, le=5000000)
    seed: int | None = None


class OptimizerInput(BaseModel):
    duration_seconds: int = Field(default=14 * 24 * 3600, gt=0)
    current_levels: list[int] = Field(default_factory=lambda: [0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
    resource_counts: list[float] = Field(
        default_factory=lambda: [0.0] * 12,
        description="Game-visible resource values.",
    )
    unlocked_pets: int = Field(default=0, ge=0)
    dungeon_levels: int = Field(default=0, ge=0)
    adventure_level: int = Field(default=0, ge=0)
    weights: dict[str, float] = Field(default_factory=lambda: {
        "event_currency": 100e-5,
        "free_exp": 6e-5,
        "pet_stones": 3.5e-5,
        "research_points": 2e-5,
        "growth": 8e-5,
    })
    busy_times: list[BusyTime] = Field(default_factory=list)
    starting_path: list[int] = Field(default_factory=list)

    @field_validator("current_levels")
    @classmethod
    def validate_levels(cls, value: list[int]) -> list[int]:
        if len(value) != 25:
            raise ValueError("current_levels must contain 25 values")
        return value

    @field_validator("resource_counts")
    @classmethod
    def validate_counts(cls, value: list[float]) -> list[float]:
        if len(value) != 12:
            raise ValueError("resource_counts must contain 12 values")
        return value


class Purchase(BaseModel):
    index: int
    upgrade_type: int
    upgrade_name: str
    elapsed_seconds: float
    level_after: int


class TimelineSnapshot(BaseModel):
    elapsed_seconds: float
    levels: list[int]
    resources: list[float]
    production_rates: list[float]


class ScoreHistoryPoint(BaseModel):
    elapsed_seconds: float = Field(ge=0)
    score: float


class SearchStats(BaseModel):
    elapsed_seconds: float = Field(ge=0)
    iterations: int = Field(ge=0)
    stop_reason: Literal["time_limit", "iteration_limit", "user_stop"] | None = None
    score_history: list[ScoreHistoryPoint] = Field(default_factory=list)
    last_improvement_elapsed_seconds: float | None = Field(default=None, ge=0)


class OptimizerResult(BaseModel):
    engine_id: str
    engine_version: str
    score: float
    path: list[int]
    purchases: list[Purchase]
    final_levels: list[int]
    final_resources: list[float]
    snapshots: list[TimelineSnapshot]
    stopped: bool = False
    search_stats: SearchStats | None = None


class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    engine_id: str = "cpp-local-search"
    input: OptimizerInput = Field(default_factory=OptimizerInput)
    limits: RunLimits | None = None


class Profile(ProfileIn):
    id: int
    engine_version: str


class ProfilePage(BaseModel):
    items: list[Profile]
    total: int
    page: int
    page_size: int


class RunCreate(BaseModel):
    profile_id: int | None = None
    engine_id: str = "cpp-local-search"
    input: OptimizerInput
    limits: RunLimits = Field(default_factory=RunLimits)


class SimulateRequest(BaseModel):
    input: OptimizerInput
    path: list[int]


class RunRecord(BaseModel):
    id: int
    profile_id: int | None
    engine_id: str
    engine_version: str
    status: Literal["queued", "running", "completed", "stopped", "failed", "interrupted"]
    score: float | None = None
    input: OptimizerInput
    limits: RunLimits | None = None
    result: OptimizerResult | None = None
    error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RunPage(BaseModel):
    items: list[RunRecord]
    total: int
    page: int
    page_size: int


class OcrParseResult(BaseModel):
    levels: list[int | None] = Field(min_length=12, max_length=12)
    speeds: list[int | None] = Field(min_length=12, max_length=12)
    counts: list[int | None] = Field(min_length=12, max_length=12)
    duration_seconds: int | None = Field(default=None, ge=0)
    warnings: list[str] = Field(default_factory=list)
    complete: bool = False
