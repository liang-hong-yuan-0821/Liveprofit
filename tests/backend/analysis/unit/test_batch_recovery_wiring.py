# test-catalog-begin
# {
#   "purpose": "分析任务 / batch_recovery_wiring（批次）：Dispatcher recovery wiring without PG, Redis or message publication.",
#   "keywords": [
#     "分析任务",
#     "批次",
#     "任务",
#     "batch_recovery_wiring",
#     "batch",
#     "task"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/application/batch_completion.py",
#     "backend/workers/dispatcher.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""Dispatcher recovery wiring without PG, Redis or message publication."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.workers import dispatcher
from backend.modules.quant_strategy.application import batch_completion


@pytest.mark.parametrize("recovery_fails", [False, True])
def test_dispatcher_recovers_batches_after_task_leases(monkeypatch, recovery_fails):
    calls = []

    @contextmanager
    def uow(_factory):
        yield object()

    task_service = SimpleNamespace(recover_expired_leases=lambda **kwargs: calls.append("leases") or 3)
    monkeypatch.setattr(dispatcher, "SqlAlchemyAnalysisUnitOfWork", uow)
    monkeypatch.setattr(dispatcher, "RedisTaskEventStream", Mock())
    monkeypatch.setattr(dispatcher, "TaskEventService", Mock())
    monkeypatch.setattr(dispatcher, "TaskService", lambda *a, **k: task_service)

    def recover(factory):
        calls.append("batches")
        if recovery_fails:
            raise RuntimeError("temporary outage")
        return 2

    monkeypatch.setattr(batch_completion, "recover_batches", recover)
    runtime = object.__new__(dispatcher.DispatcherRuntime)
    runtime._container = SimpleNamespace(session_factory=object(), redis=object())
    runtime._settings = SimpleNamespace(
        core=SimpleNamespace(stream_maxlen=100, recovery_grace_seconds=60),
        worker=SimpleNamespace(lease_ttl_seconds=60),
    )
    assert runtime.recover_expired_leases() == 3
    assert calls == ["leases", "batches"]
