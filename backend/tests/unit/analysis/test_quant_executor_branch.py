"""Worker 量化分支冒烟（plan 4.5.1 盲点 2/3）：_is_quant_task 判定与 _on_progress 三参兼容。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from backend.modules.analysis.application.contracts import ClaimedTask
from backend.modules.analysis.domain.enums import TaskStatus, TaskType
from backend.workers.analysis_executor import AnalysisExecutor, _is_quant_task


def _claimed(**overrides) -> ClaimedTask:
    base = dict(
        task_id=uuid.uuid4(),
        attempt_no=1,
        lease_token="lease-1",
        task_type=TaskType.MARKET_WIDE,
        ticker=None,
        selected_layers=("position",),
        effective_trade_date=None,
        request_params={},
    )
    base.update(overrides)
    return ClaimedTask(**base)


def test_is_quant_task_discriminator():
    quant = _claimed(request_params={"execution_snapshot": {"strategy": {}}})
    assert _is_quant_task(quant) is True
    assert _is_quant_task(_claimed(selected_layers=("market",))) is False
    assert _is_quant_task(_claimed(request_params={"analysis_options": {}})) is False
    # 同选 AI 层仍是量化任务（AI 层照旧运行，报告合并）
    assert _is_quant_task(_claimed(selected_layers=("market", "position"), request_params={"execution_snapshot": {}})) is True


@dataclass
class _FakeTaskRow:
    status: str = "RUNNING"
    lease_token: str = "lease-1"


class _FakeBundle:
    """最小 bundle：uow.tasks.get + events.publish。"""

    def __init__(self, task: _FakeTaskRow) -> None:
        self.task = task
        self.published: list[tuple] = []
        self.marked_cancelled: list[tuple] = []
        self.uow = SimpleNamespace(tasks=SimpleNamespace(get=lambda task_id: self.task))
        self.tasks = SimpleNamespace(
            mark_cancelled=lambda task_id, attempt_no, lease_token: self.marked_cancelled.append((task_id, attempt_no))
        )
        self.events = SimpleNamespace(publish=lambda *a, **kw: self.published.append((a, kw)))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeBundleFactory:
    def __init__(self, bundle: _FakeBundle) -> None:
        self.bundle = bundle

    def open(self):
        return self.bundle


def _executor(bundle: _FakeBundle) -> AnalysisExecutor:
    return AnalysisExecutor(
        graph_adapter=None,
        bundle_factory=_FakeBundleFactory(bundle),
        worker_id="w1",
        heartbeat_interval_seconds=3600,
    )


def test_on_progress_three_arg_quant_form_publishes_message():
    """量化扫描按 (stage, done, total) 三参调用（B2 回归保护）：归一为消息并发布。"""
    bundle = _FakeBundle(_FakeTaskRow())
    executor = _executor(bundle)
    executor._claimed = _claimed()  # noqa: SLF001

    executor._on_progress("scan", 200, 5300)

    assert len(bundle.published) == 1
    assert bundle.published[0][1]["message"] == "scan 200/5300"
    assert bundle.published[0][1]["attempt_no"] == 1


def test_on_progress_single_arg_graph_form_still_works():
    """图路径单参调用不受影响。"""
    bundle = _FakeBundle(_FakeTaskRow())
    executor = _executor(bundle)
    executor._claimed = _claimed()  # noqa: SLF001

    executor._on_progress("market 阶段完成")

    assert bundle.published[0][1]["message"] == "market 阶段完成"


def test_on_progress_cancel_requested_raises_and_marks_cancelled():
    bundle = _FakeBundle(_FakeTaskRow(status=TaskStatus.CANCEL_REQUESTED.value))
    executor = _executor(bundle)
    executor._claimed = _claimed()  # noqa: SLF001

    from backend.modules.analysis.application.errors import CooperativeCancelledError

    import pytest

    with pytest.raises(CooperativeCancelledError):
        executor._on_progress("scan", 1, 1)  # noqa: SLF001
    assert executor._cancel_flag is True  # noqa: SLF001
