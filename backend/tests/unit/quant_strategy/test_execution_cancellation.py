"""取消时不再登记进程，不提交未完成批次；无需真实数据库。"""

import uuid
import hashlib
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.modules.analysis.infrastructure.execution_control import ExecutionControl, ExecutionInactiveError
from backend.modules.quant_strategy.application.execution import QuantExecutionService
from backend.modules.quant_strategy.application import execution


def control(**overrides):
    return ExecutionControl(
        uuid.uuid4(), "lease", overrides.get("is_cancelled", lambda: False),
        overrides.get("is_fencing_active", lambda: False),
    )


@pytest.mark.parametrize("inactive", ["is_cancelled", "is_fencing_active"])
def test_inactive_control_refuses_new_process(inactive):
    ctl = control(**{inactive: lambda: True})
    with pytest.raises(ExecutionInactiveError):
        ctl.register_process(Mock())


def test_termination_prevents_late_registration_and_is_idempotent():
    ctl = control()
    proc = Mock()
    proc.poll.return_value = 0
    ctl.register_process(proc)
    ctl.terminate_all()
    ctl.terminate_all()
    proc.wait.assert_called_once()
    with pytest.raises(ExecutionInactiveError):
        ctl.register_process(Mock())


def test_fatal_execution_discards_pending_transaction(monkeypatch):
    session, ctl = Mock(), Mock()
    service = QuantExecutionService(
        task_id=uuid.uuid4(), attempt_no=1, snapshot={}, market_conn=Mock(),
        session=session, execution_control=ctl, effective_trade_date=date(2026, 9, 19),
    )
    monkeypatch.setattr(service, "_run", Mock(side_effect=ExecutionInactiveError("cancelled")))
    with pytest.raises(ExecutionInactiveError):
        service.run()
    ctl.terminate_all.assert_called_once()
    session.rollback.assert_called_once()
    session.commit.assert_not_called()


def test_cancel_after_signal_staged_rolls_back_before_batch_commit(monkeypatch):
    source = '''def strategy(context):
    return {"action": "BUY", "score": 90, "entry_price": 10,
            "stop_loss": 9, "take_profit": 13, "sell_ratio": None, "reason": "test"}
'''
    cancelled = False
    ctl = control(is_cancelled=lambda: cancelled)
    session, market = Mock(), Mock()
    market.execute.return_value.fetchall.return_value = []
    monkeypatch.setattr(execution.AllMarketUniverseBuilder, "list_active_cn_stocks", lambda conn: ["000001.SZ"])
    monkeypatch.setattr(
        execution.DataReadinessGate,
        "resolve",
        lambda conn, requested: SimpleNamespace(
            requested_trade_date=requested, market_as_of_trade_date=requested,
        ),
    )
    from db.instrument.dao import ingest_state
    monkeypatch.setattr(ingest_state, "is_industry_bucket_available", lambda conn: (True, None))
    monkeypatch.setattr(execution.MarketContextBatchLoader, "load_batch", lambda *a, **k: [{
        "ts_code": "000001.SZ", "status": "OK",
        "context": {"position": {"shares": 0}, "ohlcv": {"close": [10]}},
        "execution_market": {"raw_close": 10},
    }])
    monkeypatch.setattr(execution, "run_strategy", lambda *a, **k: SimpleNamespace(
        ok=True, output={"action": "BUY", "score": 90, "entry_price": 10,
                         "stop_loss": 9, "take_profit": 13, "sell_ratio": None, "reason": "test"},
    ))
    service = QuantExecutionService(
        task_id=uuid.uuid4(), attempt_no=1,
        snapshot={"strategy": {"source_code": source, "source_hash": hashlib.sha256(source.encode()).hexdigest()}, "positions": []},
        market_conn=market, session=session, execution_control=ctl, effective_trade_date=date(2026, 9, 19),
    )

    def stage_then_cancel(signal):
        nonlocal cancelled
        cancelled = True

    session.add.side_effect = stage_then_cancel
    with pytest.raises(ExecutionInactiveError):
        service.run()
    session.add.assert_called_once()
    session.rollback.assert_called_once()
    session.commit.assert_not_called()
