"""Worker 进程装配（组合根）：sync 容器 + 服务 bundle 工厂 + 执行器。

心跳、图执行、进度发布、终态写入各 open 一个独立 Session（§3.1.4），
绝不跨线程/进程传递 Session 或 Redis client。
"""

from __future__ import annotations

import socket
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from backend.bootstrap.container import SyncContainer, build_sync_container
from backend.bootstrap.settings import Settings
from backend.modules.analysis.application.reporting import ReportService
from backend.modules.analysis.application.task_events import TaskEventService
from backend.modules.analysis.application.task_lifecycle import TaskService
from backend.modules.analysis.infrastructure.redis_task_event_stream import RedisTaskEventStream
from backend.modules.analysis.infrastructure.repositories import SqlAlchemyAnalysisUnitOfWork
from backend.shared.clock import SystemClock

_worker_settings: Settings | None = None
_worker_container: SyncContainer | None = None
_artifact_builder = None


class WorkerServiceBundle:
    """一次独立 Session 的服务集合。"""

    def __init__(self, container: SyncContainer, settings: Settings, artifact_builder) -> None:
        self._container = container
        self._settings = settings
        self._artifact_builder = artifact_builder
        self._uow: SqlAlchemyAnalysisUnitOfWork | None = None

    def __enter__(self) -> "WorkerServiceBundle":
        self._uow = SqlAlchemyAnalysisUnitOfWork(self._container.session_factory).__enter__()
        self.uow = self._uow  # 执行器阶段边界检查直接读取 ORM 实体（租约字段）
        clock = SystemClock()
        stream = RedisTaskEventStream(self._container.redis, maxlen=self._settings.core.stream_maxlen)
        self.events = TaskEventService(self._uow, clock=clock, stream=stream)
        self.tasks = TaskService(
            self._uow,
            clock=clock,
            events=self.events,
            lease_ttl_seconds=self._settings.worker.lease_ttl_seconds,
            retry=self._settings.core,
        )
        self.reports = ReportService(clock=clock)
        self.build_artifact = self._artifact_builder
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._uow is not None:
            self._uow.__exit__(exc_type, exc, tb)
            self._uow = None
            self.uow = None  # 避免持有指向已关闭 Session 的陈旧引用


def configure_worker(settings: Settings, *, artifact_builder=None, graph_factory=None) -> SyncContainer:
    """Worker 进程启动装配（幂等）。artifact_builder/graph_factory 为测试注入点。"""
    global _worker_settings, _worker_container, _artifact_builder, _graph_factory
    if _worker_container is not None:
        return _worker_container
    _worker_settings = settings
    _worker_container = build_sync_container(settings, "worker")
    if artifact_builder is None:
        from backend.modules.analysis.infrastructure.artifact_builder import build_artifact_from_state

        artifact_builder = build_artifact_from_state
    if graph_factory is None:
        from backend.modules.analysis.infrastructure.real_graph_factory import make_real_graph_factory

        graph_factory = make_real_graph_factory(settings)
    _artifact_builder = artifact_builder
    _graph_factory = graph_factory
    return _worker_container


_artifact_builder = None
_graph_factory = None


def get_worker_executor():
    """默认执行器：真实 AI 图工厂 + 平台 ArtifactStore（每次执行新建图）。"""
    if _worker_container is None or _worker_settings is None:
        raise RuntimeError("Worker wiring 未初始化：请先 configure_worker()")
    from backend.modules.analysis.infrastructure.artifact_store import PlatformArtifactStore
    from backend.modules.analysis.infrastructure.trading_graph_adapter import TradingGraphAdapter
    from backend.workers.analysis_executor import AnalysisExecutor

    settings = _worker_settings
    artifact_root = Path(settings.core.artifact_root)
    if not artifact_root.is_absolute():
        artifact_root = Path.cwd() / artifact_root
    artifact_store = PlatformArtifactStore(
        artifact_root,
        max_file_bytes=settings.core.max_artifact_file_bytes,
        max_task_bytes=settings.core.max_artifact_bytes_per_task,
        retention_days=settings.core.artifact_retention_days,
    )
    from backend.bootstrap.settings import resolve_execution_logs_root
    from backend.modules.analysis.infrastructure.real_graph_factory import build_real_initial_state

    adapter = TradingGraphAdapter(
        _graph_factory,
        initial_state_factory=lambda task: build_real_initial_state(
            task, execution_logs_root=resolve_execution_logs_root(settings.core)),
    )
    return AnalysisExecutor(
        graph_adapter=adapter,
        bundle_factory=worker_bundle_factory(),
        worker_id=worker_id(),
        heartbeat_interval_seconds=settings.worker.heartbeat_interval_seconds,
        max_attempt_runtime_seconds=settings.worker.max_attempt_runtime_seconds,
        artifact_store=artifact_store,
        core_version="0.1.0",
    )


def worker_bundle_factory() -> AbstractContextManager[WorkerServiceBundle]:
    if _worker_container is None or _worker_settings is None:
        raise RuntimeError("Worker wiring 未初始化：请先 configure_worker()")
    container, settings, builder = _worker_container, _worker_settings, _artifact_builder

    def open_bundle() -> WorkerServiceBundle:
        return WorkerServiceBundle(container, settings, builder)

    return _BareFactory(open_bundle)


class _BareFactory(AbstractContextManager):
    def __init__(self, opener) -> None:
        self._opener = opener

    def open(self):
        return self._opener()

    def __enter__(self):  # pragma: no cover - 仅协议兼容
        return self.open()

    def __exit__(self, *args):  # pragma: no cover
        return None


def worker_id() -> str:
    return f"{socket.gethostname()}:{__import__('os').getpid()}"
