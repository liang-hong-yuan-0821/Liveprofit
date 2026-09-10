"""API 进程的分析服务访问器（§3.1.3 复用同一领域状态机）。

实现说明：API 进程的服务调用经**独立有界线程池**执行——每次调用打开全新 sync Session，
绝不跨线程/跨 event loop 传递 Session 或 Redis client；行为约束与方案 §2.3 的
async 应用服务一致（复用同一状态机、Command/Result 与条件更新语义）。
SSE 的阻塞读取使用 async Redis（见 analysis_events 路由）。
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

from backend.bootstrap.container import ApiContainer
from backend.modules.analysis.application.agent_prompts import AgentPromptService
from backend.modules.analysis.application.reporting import ReportService
from backend.modules.analysis.application.task_events import TaskEventService
from backend.modules.analysis.application.task_lifecycle import TaskService
from backend.modules.analysis.infrastructure.redis_task_event_stream import RedisTaskEventStream
from backend.modules.analysis.infrastructure.repositories import SqlAlchemyAnalysisUnitOfWork
from backend.shared.clock import SystemClock

T = TypeVar("T")


class ApiAnalysisBundle:
    """一次调用的独立 Session bundle（与 Worker 侧结构一致）。"""

    def __init__(self, container: ApiContainer) -> None:
        self._container = container

    def __enter__(self) -> "ApiAnalysisBundle":
        self._uow = SqlAlchemyAnalysisUnitOfWork(self._container.sync_session_factory).__enter__()
        clock = SystemClock()
        stream = RedisTaskEventStream(
            self._container.redis_sync, maxlen=self._container.settings.core.stream_maxlen
        )
        self.events = TaskEventService(self._uow, clock=clock, stream=stream)
        self.tasks = TaskService(
            self._uow,
            clock=clock,
            events=self.events,
            lease_ttl_seconds=self._container.settings.worker.lease_ttl_seconds,
            retry=self._container.settings.core,
        )
        self.reports = ReportService(clock=clock)
        self.prompts = AgentPromptService(self._uow, clock=clock)
        self.uow = self._uow
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._uow.__exit__(exc_type, exc, tb)


class ApiAnalysisServices:
    """API 路由专用：bundle 工厂 + 有界线程池执行。"""

    def __init__(self, container: ApiContainer, *, max_workers: int = 4) -> None:
        self._container = container
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="api-analysis"
        )

    def open(self) -> ApiAnalysisBundle:
        return ApiAnalysisBundle(self._container)

    async def run(self, fn: Callable[[], T]) -> T:
        """在线程池执行同步服务调用（全新 Session，不占用 API event loop）。"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, fn)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True)


def build_api_analysis_services(container: ApiContainer) -> ApiAnalysisServices:
    return ApiAnalysisServices(container)
