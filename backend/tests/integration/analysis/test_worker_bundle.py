"""生产 WorkerServiceBundle 回归测试（评审 B1 修复：bundle 必须暴露 uow）。

此前生产 bundle 缺 `.uow`，执行器阶段边界 `bundle.uow.tasks.get(...)` 会
AttributeError → 任务 FAILED；集成/e2e 测试全部自造 bundle，故 CI 全绿。
本用例直接走生产 bundle 完成一次完整执行闭环。
"""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace

from backend.modules.analysis.application.contracts import CreateAnalysisTaskCommand
from backend.modules.analysis.application.task_lifecycle import OutboxDispatcherService
from backend.modules.analysis.domain.enums import TaskStatus, TaskType
from backend.modules.analysis.infrastructure.trading_graph_adapter import TradingGraphAdapter
from backend.tests.unit.analysis.fakes import FakeCalendar, FakeClock
from backend.workers.analysis_executor import AnalysisExecutor
from backend.workers.wiring import WorkerServiceBundle

RETRY_CFG = type("Retry", (), {"max_retry_attempts": 3, "retry_base_delay_seconds": 60})()


class FakeContainer:
    def __init__(self, env) -> None:
        self.session_factory = env["session_factory"]
        self.redis = env["redis"]


class FakeSettings:
    def __init__(self) -> None:
        self.core = SimpleNamespace(stream_maxlen=1000, max_retry_attempts=3, retry_base_delay_seconds=60)
        self.worker = SimpleNamespace(lease_ttl_seconds=120, heartbeat_interval_seconds=30,
                                      max_attempt_runtime_seconds=3600)


class BundleFactory:
    """生产 WorkerServiceBundle 的工厂（执行器每次 open 一个独立 Session）。"""

    def __init__(self, env) -> None:
        self._container = FakeContainer(env)
        self._settings = FakeSettings()

    def open(self):
        return WorkerServiceBundle(self._container, self._settings, artifact_builder=_artifact)


def _artifact(state=None):
    from backend.modules.analysis.application.contracts import AnalysisArtifact

    return AnalysisArtifact(
        report_json={"sections": [{"block": "decision", "status": "AVAILABLE"}]},
        conclusion_summary=None, risk_flag=False, risk_hint=None, decision=None,
        artifact_uri=None, checksum=None, duration_ms=100,
    )


class FakeGraph:
    def propagate(self, init_state, progress_callback):
        progress_callback("市场层分析完成")
        return {"decision": "中性"}


class RecordingPublisher:
    def __init__(self) -> None:
        self.published = []

    def publish(self, payload: dict) -> None:
        self.published.append(payload)


def test_production_bundle_exposes_uow_and_runs_full_flow(env):
    bundle_factory = BundleFactory(env)
    with bundle_factory.open() as bundle:
        # B1 回归点：生产 bundle 必须暴露 .uow（执行器阶段边界读取 ORM 实体用）
        assert bundle.uow is not None
        assert bundle.uow.tasks is not None
        task_id = bundle.tasks.create_task(
            CreateAnalysisTaskCommand(
                task_type=TaskType.SINGLE_STOCK,
                ticker="000001.SZ",
                requested_trade_date=date(2026, 9, 4),
                selected_layers=("market", "sector", "stock"),
            ),
            idempotency_key=None,
            trace_id="trace-b1",
        ).task_id

    clock = FakeClock()
    with bundle_factory.open() as bundle:
        dispatcher = OutboxDispatcherService(
            bundle.uow, task_service=bundle.tasks, publisher=RecordingPublisher(),
            clock=clock, dispatch_lease_seconds=30,
        )
        assert dispatcher.dispatch_due(limit=10) == 1

    executor = AnalysisExecutor(
        graph_adapter=TradingGraphAdapter(lambda layers=None: FakeGraph()),
        bundle_factory=bundle_factory,
        worker_id="prod-bundle-w1",
        heartbeat_interval_seconds=0.05,
        max_attempt_runtime_seconds=3600,
    )
    from backend.workers.analysis_actor import run_analysis_task

    run_analysis_task(str(task_id), 1, bundle_factory=bundle_factory, executor=executor)

    with bundle_factory.open() as bundle:
        assert bundle.tasks.get_task(task_id).status is TaskStatus.SUCCEEDED
        assert bundle.reports.get_latest_report(bundle.uow, task_id).report_version == 1
