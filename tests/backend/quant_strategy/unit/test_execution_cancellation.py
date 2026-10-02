# test-catalog-begin
# {
#   "purpose": "取消时不再登记进程，不提交未完成批次；无需真实数据库。",
#   "keywords": [
#     "量化策略",
#     "批次",
#     "执行",
#     "策略族",
#     "幂等",
#     "市场分析",
#     "交易信号",
#     "execution_cancellation",
#     "batch",
#     "execution",
#     "family",
#     "idempotent",
#     "market",
#     "signal"
#   ],
#   "covers": [
#     "backend/modules/analysis/infrastructure/execution_control.py",
#     "backend/modules/quant_strategy/application/data_readiness.py",
#     "backend/modules/quant_strategy/application/execution.py",
#     "db/instrument/dao/ingest_state.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""取消时不再登记进程，不提交未完成批次；无需真实数据库。"""

import hashlib
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.modules.analysis.infrastructure.execution_control import (
    ExecutionControl,
    ExecutionInactiveError,
)
from backend.modules.quant_strategy.application import execution
from backend.modules.quant_strategy.application.data_readiness import DataReadinessError
from backend.modules.quant_strategy.application.execution import (
    QuantExecutionService,
    _raw_price_level,
)


def control(**overrides):
    return ExecutionControl(
        uuid.uuid4(), "lease", overrides.get("is_cancelled", lambda: False),
        overrides.get("is_fencing_active", lambda: False),
    )


def test_price_level_mapping_uses_raw_to_adjusted_ratio():
    # An ex-rights raw close of 10 with adjusted close of 5 must compare
    # against an adjusted MA of 4.8 as raw 9.6, not raw 4.8.
    ratio = Decimal("10") / Decimal("5")
    assert _raw_price_level(4.8, ratio) == Decimal("9.6")
    assert _raw_price_level(None, ratio) is None


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


@pytest.mark.parametrize("action", ["BUY", "SELL_ALL"])
@pytest.mark.parametrize("preflight_pending", [False, True])
def test_market_wide_gap_runs_only_holding_protection_and_blocks_buy(monkeypatch, action, preflight_pending):
    source = '''def strategy(context):
    return {"action": "BUY", "score": 90, "entry_price": 10,
            "stop_loss": 9, "take_profit": 13, "sell_ratio": None, "reason": "test"}
'''
    session, market = Mock(), Mock()
    session.scalars.return_value = []
    session.scalar.return_value = None
    market.execute.side_effect = lambda sql, *a: SimpleNamespace(
        fetchall=lambda: [("000001.SZ",)] if "FROM market.instrument WHERE" in sql else [],
    )
    if preflight_pending:
        gate = Mock(return_value=SimpleNamespace(
            requested_trade_date=date(2026, 9, 19), market_as_of_trade_date=date(2026, 9, 19),
        ))
    else:
        gate = Mock(side_effect=DataReadinessError("missing qfq"))
    monkeypatch.setattr(execution.DataReadinessGate, "resolve", gate)
    universe = Mock(side_effect=AssertionError("full scan must not run"))
    monkeypatch.setattr(execution.AllMarketUniverseBuilder, "list_active_cn_stocks", universe)
    monkeypatch.setattr(execution.MarketContextBatchLoader, "load_batch", lambda *a, **k: [{
        "ts_code": "000001.SZ", "status": "OK",
        "context": {"position": {"shares": 500}, "ohlcv": {"close": [10]}},
        "execution_market": {"raw_close": 10},
    }])
    output = {"action": action, "score": 90, "entry_price": 10 if action == "BUY" else None,
              "stop_loss": 9 if action == "BUY" else None,
              "take_profit": 13 if action == "BUY" else None,
              "sell_ratio": None, "reason": "test"}
    monkeypatch.setattr(execution, "run_strategy", lambda *a, **k: SimpleNamespace(ok=True, output=output))
    from db.instrument.dao import ingest_state

    monkeypatch.setattr(ingest_state, "is_industry_bucket_available", lambda conn: (False, None))
    planner_summary = SimpleNamespace(
        suggested_buy_orders=0, suggested_sell_orders=0, warnings=[],
        portfolio_open_risk=0, daily_new_risk=0, buy_rejections=[],
        valued_at=date(2026, 9, 19),
    )
    planner = Mock()
    planner.plan.return_value = planner_summary
    monkeypatch.setattr(execution, "PositionPlanner", lambda repository: planner)
    service = QuantExecutionService(
        task_id=uuid.uuid4(), attempt_no=1,
        snapshot={
            "strategy": {"source_code": source, "source_hash": hashlib.sha256(source.encode()).hexdigest(),
                         "version_no": 1},
            "positions": [{"symbol": "000001.SZ", "quantity": 500, "average_cost": "10"}],
            "portfolio": {"id": str(uuid.uuid4()), "name": "test", "version": 1,
                          "total_assets": "100000", "available_cash": "50000", "risk": {}},
        },
        market_conn=market, session=session, execution_control=control(),
        effective_trade_date=date(2026, 9, 19),
        protection_only=preflight_pending,
    )
    monkeypatch.setattr(service, "_load_holding_closes", lambda codes, *, valuation_date: {})
    monkeypatch.setattr(service, "_persist_suggested_orders", lambda *a, **k: None)
    result = service.run()
    universe.assert_not_called()
    gate.assert_called_once()
    assert result["global_data_ready"] is False
    assert result["summary"]["buy_matches"] == 0
    assert "持仓保护" in result["warnings"][0]
    signal = session.add.call_args.args[0]
    if action == "BUY":
        assert signal.error_code == "NEW_RISK_BLOCKED_DATA_READINESS"
    else:
        assert signal.action == "SELL_ALL" and signal.signal_kind == "HOLDING"
        assert signal.execution_market["batch_context"]["new_risk_allowed"] is False
        assert type(signal.execution_market["batch_context"]["industry_bucket_available"]) is bool


def test_holding_valuation_uses_declared_common_date_not_request_date():
    market = Mock()
    market.execute.return_value.fetchall.return_value = [("000001.SZ", "10")]
    service = QuantExecutionService(
        task_id=uuid.uuid4(), attempt_no=1, snapshot={}, market_conn=market,
        session=Mock(), execution_control=Mock(), effective_trade_date=date(2026, 9, 27),
    )
    assert service._load_holding_closes(["000001.SZ"], valuation_date=date(2026, 9, 24)) == {
        "000001.SZ": Decimal(10)}
    sql, params = market.execute.call_args.args
    assert "trade_date = %s" in sql
    assert params == (["000001.SZ"], "2026-09-24")


@pytest.mark.parametrize("mode", [None, "typo", True, 1, {}])
def test_invalid_frozen_batch_mode_is_fatal(mode):
    with pytest.raises(execution.StrategySnapshotInvalidError):
        QuantExecutionService(
            task_id=uuid.uuid4(), attempt_no=1, snapshot={"new_risk_mode": mode},
            market_conn=Mock(), session=Mock(), execution_control=Mock(),
            effective_trade_date=date(2026, 9, 19),
        )


def test_frozen_batch_mode_cannot_be_disabled_by_call_argument():
    service = QuantExecutionService(
        task_id=uuid.uuid4(), attempt_no=1, snapshot={"new_risk_mode": "FAMILY_BATCH"},
        market_conn=Mock(), session=Mock(), execution_control=Mock(),
        effective_trade_date=date(2026, 9, 19), defer_new_risk=False,
    )
    assert service._defer_new_risk is True


@pytest.mark.parametrize("owned", [False, True])
def test_family_scan_processes_only_owner_with_its_frozen_window(monkeypatch, owned):
    source = """def strategy(context):
    return {"action": "HOLD", "score": 90, "entry_price": None,
            "stop_loss": None, "take_profit": None, "sell_ratio": None, "reason": "test"}
"""
    scanner, owner = uuid.uuid4(), uuid.uuid4()
    if owned:
        owner = scanner
    session, market = Mock(), Mock()
    session.scalars.return_value = [SimpleNamespace(symbol="000001.SZ", id=uuid.uuid4(), strategy_version_id=owner)]
    market.execute.side_effect = lambda sql, *a: SimpleNamespace(
        fetchall=lambda: [("000001.SZ",)] if "FROM market.instrument WHERE" in sql else [])
    monkeypatch.setattr(execution.AllMarketUniverseBuilder, "list_active_cn_stocks", lambda conn: ["000001.SZ"])
    monkeypatch.setattr(execution.DataReadinessGate, "resolve", lambda conn, day: SimpleNamespace(
        requested_trade_date=day, market_as_of_trade_date=day))
    from db.instrument.dao import ingest_state
    monkeypatch.setattr(ingest_state, "is_industry_bucket_available", lambda conn: (True, None))
    windows = []
    class Loader:
        def __init__(self, lookback):
            windows.append(lookback)
        def load_batch(self, *a, **k):
            return [{"ts_code": "000001.SZ", "status": "OK",
                "context": {"position": {"shares": 100}, "ohlcv": {"close": [10], "high": [10], "volume": [100]},
                            "indicators": {}},
                "execution_market": {"trade_date": "2026-09-22", "raw_close": 10, "qfq_close": 10}}]
    monkeypatch.setattr(execution, "MarketContextBatchLoader", Loader)
    monkeypatch.setattr(execution, "run_strategy", lambda *a, **k: SimpleNamespace(ok=True, output={
        "action": "HOLD", "score": 90, "entry_price": None, "stop_loss": None,
        "take_profit": None, "sell_ratio": None, "reason": "test"}))
    process = Mock(return_value=(None, None, None, None))
    monkeypatch.setattr(execution.PositionLifecycleManager, "process_day", process)
    service = QuantExecutionService(task_id=uuid.uuid4(), attempt_no=1,
        snapshot={"new_risk_mode": "FAMILY_BATCH", "strategy": {"version_id": str(scanner),
            "source_code": source, "source_hash": hashlib.sha256(source.encode()).hexdigest(), "required_bars": 123},
            "portfolio": {"id": str(uuid.uuid4())},
            "positions": [{"symbol": "000001.SZ", "market": "CN", "quantity": 100, "average_cost": 10}]},
        market_conn=market, session=session, execution_control=control(), effective_trade_date=date(2026,9,22), scan_only=True)
    monkeypatch.setattr(service, "_load_holding_closes", lambda *a, **k: {"000001.SZ": Decimal(10)})
    service.run()
    assert windows == [123]
    assert process.call_count == int(owned)
    if owned:
        assert process.call_args.kwargs["defer_buy"] is True
        assert process.call_args.kwargs["fact"]["asset_scope"] == "CN_STOCK"
        assert process.call_args.kwargs["fact"]["source_task_id"] == str(service._task_id)
