"""状态机与事务边界守卫单测。"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.modules.analysis.domain.enums import TaskStatus
from backend.modules.analysis.domain.state_machine import (
    ALLOWED_TRANSITIONS,
    InvalidStateTransitionError,
    can_transition,
    is_terminal,
)

REPOSITORIES_PATH = (
    Path(__file__).resolve().parents[4] / "backend" / "modules" / "analysis" / "infrastructure" / "repositories.py"
)


def test_transition_map_matches_plan_state_machine():
    assert ALLOWED_TRANSITIONS[TaskStatus.PENDING] == frozenset({TaskStatus.QUEUED})
    assert ALLOWED_TRANSITIONS[TaskStatus.QUEUED] == frozenset({TaskStatus.RUNNING})
    assert ALLOWED_TRANSITIONS[TaskStatus.RUNNING] == frozenset(
        {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.RETRYING}
    )
    assert ALLOWED_TRANSITIONS[TaskStatus.RETRYING] == frozenset({TaskStatus.QUEUED})
    assert ALLOWED_TRANSITIONS[TaskStatus.CANCEL_REQUESTED] == frozenset({TaskStatus.CANCELLED})
    for terminal in (TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED):
        assert ALLOWED_TRANSITIONS[terminal] == frozenset()
        assert is_terminal(terminal)
    # 终态不可回退
    assert not can_transition(TaskStatus.SUCCEEDED, TaskStatus.RUNNING)
    with pytest.raises(InvalidStateTransitionError):
        from backend.modules.analysis.domain.state_machine import assert_transition

        assert_transition(TaskStatus.FAILED, TaskStatus.QUEUED)


def test_repositories_never_commit():
    """Repository 不 commit：事务边界唯一归属 UoW（架构守卫）。

    repositories.py 中唯一允许的 commit 调用是 SqlAlchemyAnalysisUnitOfWork 内部
    （self._session.commit()，由 Application Service 经由 uow.commit() 触发）。
    """
    source = REPOSITORIES_PATH.read_text(encoding="utf-8")
    commit_calls = [
        line.strip()
        for line in source.splitlines()
        if "commit(" in line
        and "uow.commit" not in line
        and "def commit" not in line
        and not line.strip().startswith('"""')
    ]
    assert commit_calls == ["self._session.commit()"], f"发现违规 commit 调用：{commit_calls}"
