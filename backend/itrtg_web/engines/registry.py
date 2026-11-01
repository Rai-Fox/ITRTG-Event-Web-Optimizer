from __future__ import annotations

from itrtg_web.engines.base import OptimizerEngine
from itrtg_web.engines.cpp_local_search import CppLocalSearchEngine


class EngineRegistry:
    def __init__(self, engines: list[OptimizerEngine]) -> None:
        self._engines = {engine.metadata().id: engine for engine in engines}

    def list(self) -> list[OptimizerEngine]:
        return list(self._engines.values())

    def get(self, engine_id: str) -> OptimizerEngine:
        try:
            return self._engines[engine_id]
        except KeyError as exc:
            raise ValueError(f"Unknown optimizer engine: {engine_id}") from exc


def build_registry() -> EngineRegistry:
    return EngineRegistry([CppLocalSearchEngine()])
