"""TaskService.rerun_task 单测（单Agent重跑与提示词编辑方案 3.4）。

终态→PENDING+attempt+1+outbox payload 含 rerun_from；非终态 409；
并发冲突 409；claim 写/清列；重试消息（rerun_from=None）清列。
"""

from __future__ import annotations

from datetime import date

import pytest

from backend.modules.analysis.application.contracts import CreateAnalysisTaskCommand
from backend.modules.analysis.application.errors import (
    InvalidStateConflictError,
    TaskNotTerminalError,
)
from backend.modules.analysis.domain.enums import TaskStatus, TaskType
from backend.tests.unit.analysis.fakes import FakeClock, FakeUnitOfWork
from backend.tests.unit.analysis.test_task_service import _service, _create_task


def _to_terminal(service, task_id: str, status: TaskStatus, attempt_no: int = 1):
    """把任务直接推进到指定终态（经 conditional_update，绕过正常流转）。"""
    uow = service._uow
    ok = uow.tasks.conditional_update(
        task_id,
        expect={"attempt_no": attempt_no},
        changes={"status": status.value, "finished_at": FakeClock().now()},
    )
    assert ok
    uow.commit()


def _to_queued(service, task_id: str, attempt_no: int):
    """把 PENDING 任务推进到 QUEUED（模拟 dispatcher confirm）。"""
    uow = service._uow
    ok = uow.tasks.conditional_update(
        task_id,
        expect={"attempt_no": attempt_no},
        changes={"status": TaskStatus.QUEUED.value},
    )
    assert ok
    uow.commit()


def test_rerun_terminal_task_transitions_to_pending_with_payload():
    service, uow, _ = _service(events=False)
    task_id = _create_task(service, key="rerun-1")
    _to_terminal(service, task_id, TaskStatus.FAILED)

    dto = service.rerun_task(task_id, "market:CN News Analyst")

    assert dto.status == TaskStatus.PENDING
    assert dto.attempt_no == 2
    assert dto.rerun_from_node_id == "market:CN News Analyst"
    outbox = uow.outbox.get_by_task_attempt(task_id, 2)
    assert outbox is not None
    assert outbox.payload == {
        "task_id": str(task_id),
        "attempt_no": 2,
        "rerun_from": "market:CN News Analyst",
        "base_attempt": 1,
    }


def test_rerun_non_terminal_raises():
    service, uow, _ = _service(events=False)
    task_id = _create_task(service, key="rerun-2")
    with pytest.raises(TaskNotTerminalError):
        service.rerun_task(task_id, "market:CN News Analyst")


def test_rerun_concurrent_conflict_raises(monkeypatch):
    """并发防护：条件更新失败（状态/attempt 已变化）→ 409 域错误。"""
    service, uow, _ = _service(events=False)
    task_id = _create_task(service, key="rerun-3")
    _to_terminal(service, task_id, TaskStatus.SUCCEEDED)
    monkeypatch.setattr(
        uow.tasks, "conditional_update", lambda *a, **k: False)  # 模拟并发竞争丢失
    with pytest.raises(InvalidStateConflictError):
        service.rerun_task(task_id, "market:CN News Analyst")


def test_claim_writes_and_clears_rerun_column():
    """rerun 消息 claim 写列；普通/重试消息 claim 清列（展示归零）。"""
    service, uow, _ = _service(events=False)
    task_id = _create_task(service, key="rerun-4")
    _to_terminal(service, task_id, TaskStatus.FAILED)
    service.rerun_task(task_id, "market:CN News Analyst")
    _to_queued(service, task_id, 2)

    claimed = service.claim_for_execution(task_id, 2, "worker-1", rerun_from="market:CN News Analyst")
    assert claimed is not None
    assert claimed.rerun_from_node_id == "market:CN News Analyst"
    assert uow.tasks.get(task_id).rerun_from_node_id == "market:CN News Analyst"

    # 重试消息（payload 无 rerun_from）→ claim 清列
    uow.tasks.conditional_update(task_id, expect={"attempt_no": 2}, changes={
        "status": TaskStatus.QUEUED.value, "attempt_no": 3})
    uow.commit()
    claimed2 = service.claim_for_execution(task_id, 3, "worker-1")
    assert claimed2 is not None
    assert claimed2.rerun_from_node_id is None
    assert uow.tasks.get(task_id).rerun_from_node_id is None


def test_rerun_chain_after_rerun_attempt():
    """链式重跑：重跑 attempt 终态后可再次重跑（新 attempt 有自己的基座）。"""
    service, uow, _ = _service(events=False)
    task_id = _create_task(service, key="rerun-5")
    _to_terminal(service, task_id, TaskStatus.FAILED)
    service.rerun_task(task_id, "market:CN News Analyst")
    _to_terminal(service, task_id, TaskStatus.SUCCEEDED, attempt_no=2)
    # 第二次重跑基于 attempt 2
    dto = service.rerun_task(task_id, "sector:Sector News Analyst")
    assert dto.attempt_no == 3
    outbox = uow.outbox.get_by_task_attempt(task_id, 3)
    assert outbox.payload["base_attempt"] == 2
