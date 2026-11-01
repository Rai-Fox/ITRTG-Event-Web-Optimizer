from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from threading import Event

from itrtg_web.models import EngineMetadata, OptimizerInput, OptimizerResult, RunLimits, TimelineSnapshot

ProgressCallback = Callable[[OptimizerResult], None]


class OptimizerEngine(ABC):
    @abstractmethod
    def metadata(self) -> EngineMetadata:
        raise NotImplementedError

    @abstractmethod
    def schema(self) -> dict:
        raise NotImplementedError

    @abstractmethod
    def validate(self, input_data: OptimizerInput) -> None:
        raise NotImplementedError

    @abstractmethod
    def simulate(self, input_data: OptimizerInput, path: list[int]) -> OptimizerResult:
        raise NotImplementedError

    @abstractmethod
    def optimize(
        self,
        input_data: OptimizerInput,
        limits: RunLimits,
        stop_event: Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> OptimizerResult:
        raise NotImplementedError

    @abstractmethod
    def timeline(self, result: OptimizerResult, t_seconds: float) -> TimelineSnapshot:
        raise NotImplementedError
