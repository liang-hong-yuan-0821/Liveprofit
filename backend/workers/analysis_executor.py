"""分析执行器（§3.1.4「Worker 执行器职责」）。

- 心跳、图执行、进度发布、终态写入均使用独立 Session（每次 open 一个新 bundle）。
- 心跳续租失败（fencing 丢失）或到达 attempt deadline 后，执行器停止提交任何状态/产物。
- 协作式取消：进度回调边界检查 CANCEL_REQUESTED，发现即 mark_cancelled 并停止后续图推进。
- 业务异常经 classify_error → fail_or_retry（Dramatiq 不做业务重试）。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from contextlib import AbstractContextManager
from typing import Any, Callable, Protocol

from backend.modules.analysis.application.contracts import AnalysisArtifact, ClaimedTask
from backend.modules.analysis.application.errors import (
    CooperativeCancelledError,
    FencingLostError,
    LeaseConflictError,
    classify_error,
)
from backend.modules.analysis.domain.enums import TaskEventType, TaskStatus
from backend.modules.analysis.infrastructure.artifact_store import ArtifactFencingError

logger = logging.getLogger(__name__)

PhaseExtractor = Callable[[str], str | None]
ArtifactBuilder = Callable[[Any], AnalysisArtifact]


class ServiceBundle(Protocol):
    """一次独立 Session 的完整服务集合（心跳/进度/终态各自 open）。"""

    tasks: Any  # TaskService
    reports: Any  # ReportService
    events: Any  # TaskEventService
    build_artifact: ArtifactBuilder


class ServiceBundleFactory(Protocol):
    def open(self) -> AbstractContextManager[ServiceBundle]: ...


def default_phase_extractor(message: str) -> str | None:
    """首版只归类市场/板块/个股/决策的稳定阶段消息（不解析日志文本）。"""
    for phase, keywords in (
        ("market", ("市场", "market")),
        ("sector", ("板块", "sector")),
        ("stock", ("个股", "stock")),
        ("decision", ("决策", "decision")),
    ):
        if any(keyword in message for keyword in keywords):
            return phase
    return None


class LeaseHeartbeat(threading.Thread):
    """独立心跳线程：每个 tick 都用独立 Session 调 renew_lease（不与 Actor/进度共享 Session）。"""

    def __init__(
        self,
        *,
        task_id: uuid.UUID,
        attempt_no: int,
        lease_token: str,
        bundle_factory: ServiceBundleFactory,
        heartbeat_interval_seconds: float,
        max_attempt_runtime_seconds: float,
    ) -> None:
        super().__init__(daemon=True, name=f"lease-heartbeat-{task_id}-{attempt_no}")
        self._task_id = task_id
        self._attempt_no = attempt_no
        self._lease_token = lease_token
        self._bundle_factory = bundle_factory
        self._interval = heartbeat_interval_seconds
        self._deadline = time.monotonic() + max_attempt_runtime_seconds
        # 注意：不可覆盖 Thread 内部方法名 _stop（join 时会被调用）
        self._stop_requested = threading.Event()
        self.fencing_lost = threading.Event()

    def run(self) -> None:
        while not self._stop_requested.wait(self._interval):
            if time.monotonic() > self._deadline:
                # 到达 attempt deadline：停止续租，Actor 在阶段边界协作停止
                self.fencing_lost.set()
                return
            ok = False
            try:
                with self._bundle_factory.open() as bundle:
                    ok = bundle.tasks.renew_lease(self._task_id, self._attempt_no, self._lease_token)
            except Exception:  # noqa: BLE001 - 任何续租异常均视为失去 fencing
                logger.exception("心跳续租异常：task=%s attempt=%s", self._task_id, self._attempt_no)
            if not ok:
                self.fencing_lost.set()
                return

    def stop(self) -> None:
        self._stop_requested.set()


class AnalysisExecutor:
    def __init__(
        self,
        *,
        graph_adapter: Any,
        bundle_factory: ServiceBundleFactory,
        worker_id: str,
        heartbeat_interval_seconds: float = 30.0,
        max_attempt_runtime_seconds: float = 3600.0,
        phase_extractor: PhaseExtractor = default_phase_extractor,
        artifact_store: Any = None,
        core_version: str | None = None,
    ) -> None:
        self._adapter = graph_adapter
        self._bundles = bundle_factory
        self._worker_id = worker_id
        self._heartbeat_interval = heartbeat_interval_seconds
        self._max_runtime = max_attempt_runtime_seconds
        self._phase_extractor = phase_extractor
        self._artifact_store = artifact_store
        self._core_version = core_version
        self._sequence = 0
        self._cancel_flag = False

    def execute(self, claimed: ClaimedTask) -> None:
        self._claimed = claimed
        with self._bundles.open() as bundle:
            bundle.events.publish(
                claimed.task_id,
                TaskEventType.STARTED,
                attempt_no=claimed.attempt_no,
                worker_id=self._worker_id,
            )
        heartbeat = LeaseHeartbeat(
            task_id=claimed.task_id,
            attempt_no=claimed.attempt_no,
            lease_token=claimed.lease_token,
            bundle_factory=self._bundles,
            heartbeat_interval_seconds=self._heartbeat_interval,
            max_attempt_runtime_seconds=self._max_runtime,
        )
        heartbeat.start()
        try:
            self._run_graph(claimed)
        finally:
            heartbeat.stop()
            heartbeat.join(timeout=self._heartbeat_interval * 2 + 5)

    # ---- 内部流程 ----

    def _run_graph(self, claimed: ClaimedTask) -> None:
        try:
            final_state = self._adapter.execute(claimed, self._on_progress)
        except CooperativeCancelledError:
            return  # 回调中已完成 mark_cancelled
        except FencingLostError:
            logger.warning("fencing 丢失，放弃写入：task=%s attempt=%s", claimed.task_id, claimed.attempt_no)
            return
        except LeaseConflictError:
            logger.warning("租约冲突，放弃写入（迟到 attempt）：task=%s attempt=%s", claimed.task_id, claimed.attempt_no)
            return
        except Exception as exc:
            self._fail_or_retry(claimed, exc)
            return

        if self._cancel_flag:
            # 图已返回但取消标志已置位（未在回调中收口）：持有租约收口 CANCELLED
            try:
                with self._bundles.open() as bundle:
                    bundle.tasks.mark_cancelled(claimed.task_id, claimed.attempt_no, claimed.lease_token)
            except LeaseConflictError:
                logger.warning("取消收口被 fencing 拒绝：task=%s attempt=%s", claimed.task_id, claimed.attempt_no)
            return

        try:
            with self._bundles.open() as bundle:
                # 图返回后复核 DB 状态（取消请求可能在最后一次 progress 之后到达，回调已无机会检测）
                task_orm = bundle.uow.tasks.get(claimed.task_id)
                if task_orm is None:
                    raise FencingLostError()
                if task_orm.status == TaskStatus.CANCEL_REQUESTED.value:
                    self._cancel_flag = True
                if self._cancel_flag:
                    bundle.tasks.mark_cancelled(claimed.task_id, claimed.attempt_no, claimed.lease_token)
                    return
                artifact = bundle.build_artifact(final_state)
                if self._artifact_store is not None:
                    artifact = self._persist_artifact(claimed, final_state, artifact)
                bundle.tasks.complete_task(
                    claimed.task_id, claimed.attempt_no, claimed.lease_token, artifact, bundle.reports
                )
        except (LeaseConflictError, FencingLostError):
            logger.warning("完成被 fencing 拒绝（迟到 attempt）：task=%s attempt=%s", claimed.task_id, claimed.attempt_no)
        except ArtifactFencingError:
            # 最终目录已存在且不一致：过期 attempt 不得覆盖，直接放弃写入（reconciliation 兜底）
            logger.warning("产物发布 fencing 冲突，放弃本次 attempt：task=%s attempt=%s", claimed.task_id, claimed.attempt_no)
        except Exception as exc:
            self._fail_or_retry(claimed, exc)

    def _persist_artifact(self, claimed: ClaimedTask, final_state: Any, artifact: AnalysisArtifact) -> AnalysisArtifact:
        """staging 校验 → manifest → 同卷 os.replace 发布；返回带受控引用与 checksum 的 artifact。"""
        uri, checksum = self._artifact_store.persist_artifact(
            task_id=claimed.task_id,
            attempt_no=claimed.attempt_no,
            lease_token=claimed.lease_token,
            core_version=self._core_version,
            final_state=final_state if isinstance(final_state, dict) else {},
            report_json=artifact.report_json,
        )
        return AnalysisArtifact(
            report_json=artifact.report_json,
            conclusion_summary=artifact.conclusion_summary,
            risk_flag=artifact.risk_flag,
            risk_hint=artifact.risk_hint,
            decision=artifact.decision,
            artifact_uri=uri,
            checksum=checksum,
            duration_ms=artifact.duration_ms,
        )

    def _fail_or_retry(self, claimed: ClaimedTask, exc: Exception) -> None:
        logger.exception("分析执行失败，进入 fail_or_retry：task=%s attempt=%s", claimed.task_id, claimed.attempt_no)
        classified = classify_error(exc)
        try:
            with self._bundles.open() as bundle:
                bundle.tasks.fail_or_retry(claimed.task_id, claimed.attempt_no, claimed.lease_token, classified)
        except LeaseConflictError:
            logger.warning("失败/重试被 fencing 拒绝：task=%s attempt=%s", claimed.task_id, claimed.attempt_no)

    def _on_progress(self, message: str) -> None:
        """阶段边界：先检查取消标志与 fencing，再发布 progress（单一独立 Session）。"""
        claimed = self._claimed
        if self._cancel_flag:
            return
        self._sequence += 1
        with self._bundles.open() as bundle:
            task = bundle.uow.tasks.get(claimed.task_id)  # ORM 实体（含 lease_token）
            if task is None:
                raise FencingLostError()
            if task.status == TaskStatus.CANCEL_REQUESTED.value:
                self._cancel_flag = True
                bundle.tasks.mark_cancelled(claimed.task_id, claimed.attempt_no, claimed.lease_token)
                raise CooperativeCancelledError()
            if task.status != TaskStatus.RUNNING.value or task.lease_token != claimed.lease_token:
                raise FencingLostError()
            bundle.events.publish(
                claimed.task_id,
                TaskEventType.PROGRESS,
                attempt_no=claimed.attempt_no,
                sequence=self._sequence,
                phase=self._phase_extractor(message),
                message=message,
            )
