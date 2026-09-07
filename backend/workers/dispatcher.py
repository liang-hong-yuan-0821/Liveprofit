"""Outbox Dispatcher 常驻进程（§3.1.4 / §3.1.6）。

- 唯一常驻调度模型：启动先执行一次过期租约恢复，再循环 dispatch_due；
  按 recovery_interval 再次恢复租约；空闲按 poll_interval + 抖动退避。
- 可启动多个 Dispatcher，依赖 SKIP LOCKED + dispatch lease 协调。
- SIGTERM/SIGINT：停止新一轮领取、完成当前短事务后退出；
  未确认的 dispatch lease 由到期接管。
"""

from __future__ import annotations

import logging
import random
import signal
import threading
import time

from backend.bootstrap.observability import init_logging
from backend.bootstrap.settings import Settings
from backend.modules.analysis.application.task_lifecycle import OutboxDispatcherService, TaskService
from backend.modules.analysis.infrastructure.dramatiq_task_message_publisher import DramatiqTaskMessagePublisher
from backend.modules.analysis.infrastructure.repositories import SqlAlchemyAnalysisUnitOfWork
from backend.modules.analysis.infrastructure.redis_task_event_stream import RedisTaskEventStream
from backend.modules.analysis.application.task_events import TaskEventService
from backend.shared.clock import SystemClock
from backend.workers.broker import configure_broker

logger = logging.getLogger(__name__)


class DispatcherRuntime:
    def __init__(self, settings: Settings, container) -> None:
        self._settings = settings
        self._container = container
        self._publisher = DramatiqTaskMessagePublisher()
        self._artifact_store = self._build_artifact_store()

    def _build_artifact_store(self):
        from pathlib import Path

        from backend.modules.analysis.infrastructure.artifact_store import PlatformArtifactStore

        core = self._settings.core
        root = Path(core.artifact_root)
        if not root.is_absolute():
            root = Path.cwd() / root
        return PlatformArtifactStore(
            root,
            max_file_bytes=core.max_artifact_file_bytes,
            max_task_bytes=core.max_artifact_bytes_per_task,
            retention_days=core.artifact_retention_days,
        )

    def reconcile_artifacts(self) -> dict:
        """§2.4 reconciliation：过期 staging 与保留期外未引用最终目录清理。

        DB manifest 引用集合（analysis_reports.artifact_uri）绝不被删除；
        verify 失败目录只告警（报告读模型降级 UNAVAILABLE 由告警链处理）。
        """
        from sqlalchemy import text

        referenced: set[str] = set()
        with self._container.session_factory() as session:
            rows = session.execute(
                text("SELECT DISTINCT artifact_uri FROM analysis_reports WHERE artifact_uri IS NOT NULL")
            ).scalars()
            referenced = {row for row in rows if row}
        return self._artifact_store.reconcile(
            referenced=referenced,
            high_water_bytes=self._settings.core.artifact_high_water_bytes,
        )

    def recover_expired_leases(self) -> int:
        with SqlAlchemyAnalysisUnitOfWork(self._container.session_factory) as uow:
            clock = SystemClock()
            stream = RedisTaskEventStream(self._container.redis, maxlen=self._settings.core.stream_maxlen)
            events = TaskEventService(uow, clock=clock, stream=stream)
            service = TaskService(
                uow,
                clock=clock,
                events=events,
                lease_ttl_seconds=self._settings.worker.lease_ttl_seconds,
                retry=self._settings.core,
            )
            return service.recover_expired_leases(grace_seconds=self._settings.core.recovery_grace_seconds)

    def dispatch_due(self, limit: int) -> int:
        with SqlAlchemyAnalysisUnitOfWork(self._container.session_factory) as uow:
            clock = SystemClock()
            stream = RedisTaskEventStream(self._container.redis, maxlen=self._settings.core.stream_maxlen)
            events = TaskEventService(uow, clock=clock, stream=stream)
            task_service = TaskService(
                uow,
                clock=clock,
                events=events,
                lease_ttl_seconds=self._settings.worker.lease_ttl_seconds,
                retry=self._settings.core,
            )
            dispatcher = OutboxDispatcherService(
                uow, task_service=task_service, publisher=self._publisher, clock=clock, dispatch_lease_seconds=30
            )
            return dispatcher.dispatch_due(limit=limit)


def dispatcher_loop(runtime: DispatcherRuntime, *, stop_event: threading.Event) -> None:
    settings = runtime._settings
    poll_interval = settings.dispatcher.poll_interval_seconds
    batch_size = settings.dispatcher.batch_size
    recovery_interval = settings.dispatcher.recovery_interval_seconds
    idle_backoff = settings.dispatcher.idle_backoff_seconds

    # 启动先执行一次过期租约恢复
    recovered = runtime.recover_expired_leases()
    logger.info("启动恢复过期租约：%s 条", recovered)
    last_recovery = time.monotonic()

    while not stop_event.is_set():
        try:
            if time.monotonic() - last_recovery >= recovery_interval:
                recovered = runtime.recover_expired_leases()
                if recovered:
                    logger.info("恢复过期租约：%s 条", recovered)
                try:
                    cleaned = runtime.reconcile_artifacts()
                    if cleaned.get("staging") or cleaned.get("final"):
                        logger.info("产物清理：staging=%s final=%s", cleaned["staging"], cleaned["final"])
                except Exception:  # noqa: BLE001 - 产物清理失败不得阻塞投递循环
                    logger.exception("产物清理失败（不影响投递）")
                last_recovery = time.monotonic()

            dispatched = runtime.dispatch_due(limit=batch_size)
            if dispatched:
                logger.info("已投递 Outbox：%s 条", dispatched)
                wait = poll_interval
            else:
                # 空闲退避 + 抖动，避免多个 Dispatcher 同步轮询
                wait = idle_backoff * (0.8 + 0.4 * random.random())
        except Exception:  # noqa: BLE001 - 常驻循环：单轮异常记录后继续
            logger.exception("Dispatcher 轮询异常，继续下一轮")
            wait = idle_backoff
        stop_event.wait(wait)


def main() -> int:
    settings = Settings()
    settings.validate_for_process("dispatcher")
    init_logging(settings.core.log_level)

    configure_broker(settings.core.resolved_redis_url())
    from backend.bootstrap.container import build_sync_container

    container = build_sync_container(settings, "dispatcher")
    runtime = DispatcherRuntime(settings, container)
    stop_event = threading.Event()

    def _handle_signal(signum, frame):  # noqa: ARG001
        logger.info("收到信号 %s，停止新一轮领取并退出", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    logger.info("liveprofit-dispatcher 启动：poll=%.1fs batch=%s recovery=%ss",
                settings.dispatcher.poll_interval_seconds, settings.dispatcher.batch_size,
                settings.dispatcher.recovery_interval_seconds)
    try:
        dispatcher_loop(runtime, stop_event=stop_event)
    finally:
        container.dispose()
    return 0
