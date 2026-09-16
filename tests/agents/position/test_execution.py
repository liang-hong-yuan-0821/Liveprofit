"""QuantExecutionService 集成测试（plan 4.3.3）：实时扫描/信号落库/订单回写/取消/快照校验。"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from backend.modules.analysis.infrastructure.execution_control import (
    ExecutionControl,
    ExecutionInactiveError,
)
from backend.modules.quant_strategy.application.execution import (
    QuantExecutionService,
    StrategySnapshotInvalidError,
)
from db.instrument.dao import ingest_state as state_dao

EFFECTIVE = date(2026, 9, 15)
def _new_task_id() -> uuid.UUID:
    return uuid.uuid4()

STRATEGY = '''
def strategy(context):
    shares = context["position"]["shares"]
    close = context["ohlcv"]["close"]
    price = close[-1]
    if shares > 0:
        return {"action": "SELL_ALL", "score": 0, "entry_price": None,
                "stop_loss": None, "take_profit": None,
                "sell_ratio": None, "reason": "清仓"}
    if price > 1:
        return {"action": "BUY", "score": 90, "entry_price": price,
                "stop_loss": round(price * 0.9, 2), "take_profit": round(price * 1.2, 2),
                "sell_ratio": None, "reason": "买入"}
    return {"action": "HOLD", "score": 50, "entry_price": None,
            "stop_loss": None, "take_profit": None,
            "sell_ratio": None, "reason": "不动"}
'''


class _Rows:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeMarketConn:
    """按 SQL 关键字分发的伪行情连接（psycopg 风格 execute/fetchall）。"""

    def __init__(
        self,
        instruments: list[str],
        daily_rows: list,
        factor_rows: list,
        industry_rows: list,
        *,
        ingest_state_rows: list | None = None,
        closes: dict[str, float] | None = None,
    ) -> None:
        self.instruments = instruments
        self.daily_rows = daily_rows
        self.factor_rows = factor_rows
        self.industry_rows = industry_rows
        self.ingest_state_rows = ingest_state_rows or []
        self.closes = closes or {}

    def execute(self, sql: str, params=None):
        if "market.ingest_state" in sql:
            return _Rows(self.ingest_state_rows)
        if "JOIN market.industry" in sql:
            return _Rows([(r[0], r[1], r[2]) for r in self.industry_rows])
        if "market.industry_member" in sql:
            # DAO 门控查询：返回 (industry_code, ts_code) 两列
            return _Rows([(r[1], r[0]) for r in self.industry_rows])
        if "DISTINCT ON" in sql:
            return _Rows([(ts, self.closes[ts]) for ts in self.closes])
        if "market.instrument_daily" in sql:
            return _Rows(self.daily_rows)
        if "market.factor_daily" in sql:
            return _Rows(self.factor_rows)
        if "market.instrument" in sql:
            if "~" in sql:
                return _Rows([(ts,) for ts in self.instruments])
            return _Rows([(ts,) for ts in self.instruments])
        return _Rows([])


def _bars(ts_code: str, days: int, close: float, start: date = date(2026, 9, 15)):
    rows = []
    d = start
    for i in range(days):
        rows.append((ts_code, d, close - 1, close + 1, close - 0.5, close, 1000.0, close * 1000))
        d -= timedelta(days=1)
    return rows


def _factors(ts_code: str, days: int, start: date = date(2026, 9, 15)):
    rows = []
    d = start
    for i in range(days):
        rows.append((ts_code, d, 9.5, 9.8, 55.0))
        d -= timedelta(days=1)
    return rows


def _snapshot(source_code: str, positions: list[dict]) -> dict:
    return {
        "schema_version": "quant_execution_snapshot_v1",
        "strategy": {
            "strategy_id": str(uuid.uuid4()),
            "version_id": str(uuid.uuid4()),
            "version_no": 1,
            "name": "测试策略",
            "source_code": source_code,
            "source_hash": hashlib.sha256(source_code.encode("utf-8")).hexdigest(),
            "published_at": datetime.now(timezone.utc).isoformat(),
        },
        "portfolio": {
            "id": str(uuid.uuid4()),
            "name": "核心仓",
            "version": 1,
            "total_assets": "100000.0000",
            "available_cash": "35000.0000",
            "risk": {
                "risk_per_trade_pct": "0.010000",
                "min_risk_reward_ratio": "2.0000",
                "max_total_position_pct": "0.800000",
                "max_single_stock_pct": "0.100000",
                "max_sector_pct": "0.300000",
            },
        },
        "positions": positions,
    }


def _control(task_id: uuid.UUID, cancelled: bool = False) -> ExecutionControl:
    return ExecutionControl(task_id, "lease", lambda: cancelled, lambda: False)


def _make_task(env) -> uuid.UUID:
    task_id = uuid.uuid4()
    with env["session_factory"]() as session:
        session.execute(
            text(
                "INSERT INTO analysis_tasks (id, task_type, status, request_params, selected_layers, input_hash, attempt_no) "
                "VALUES (:id, 'MARKET_WIDE', 'RUNNING', '{}'::jsonb, '[\"position\"]'::jsonb, 'h', 1)"
            ),
            {"id": task_id},
        )
        session.commit()
    return task_id


def _signals_rows(env, task_id: uuid.UUID) -> list:
    with env["session_factory"]() as session:
        return [
            dict(r._mapping)
            for r in session.execute(
                text(
                    "SELECT signal_kind, ts_code, action, score, order_status, shares, notional, "
                    "error_code, entry_price, stop_loss, take_profit, sell_ratio, valuation_price "
                    "FROM quant_execution_signals WHERE task_id = :id ORDER BY id"
                ),
                {"id": task_id},
            )
        ]


def _market_conn(extra_inst: list[str] = ("000001.SZ", "000002.SZ", "600519.SH"), days: dict | None = None):
    instruments = list(extra_inst)
    days = days or {}
    daily = []
    factor = []
    # 默认 300 根（>250 检出「取最老窗口」类回归）且 close 按票递增（检出 valuation_price 串票）
    for idx, ts in enumerate(instruments):
        close = 10.0 + idx
        daily += _bars(ts, days.get(ts, 300), close)
        factor += _factors(ts, days.get(ts, 300))
    member_rows = [(ts, "801080", "电子") for ts in instruments]
    state = [
        (
            "SUCCESS",  # status
            datetime.now(timezone.utc),  # successful_at
            1.0,  # coverage
            state_dao.member_set_hash([(r[1], r[0]) for r in member_rows]),  # member_hash
            None,  # failure_code
            None,  # summary_json
            datetime.now(timezone.utc),  # observed_at
        )
    ]
    return FakeMarketConn(
        instruments, daily, factor, member_rows,
        ingest_state_rows=state, closes={ts: 10.0 + idx for idx, ts in enumerate(instruments)},
    )


def test_full_flow_buy_sell_and_orders(env):
    task_id = _make_task(env)
    positions = [{"market": "CN", "symbol": "600519.SH", "quantity": "100.0000", "average_cost": "1500.0000"}]
    snapshot = _snapshot(STRATEGY, positions)
    with env["session_factory"]() as session:
        service = QuantExecutionService(
            task_id=task_id, attempt_no=1, snapshot=snapshot,
            market_conn=_market_conn(), session=session,
            execution_control=_control(task_id), effective_trade_date=EFFECTIVE,
        )
        summary = service.run()
    assert summary["summary"]["universe_total"] == 3
    assert summary["summary"]["data_complete"] == 3
    assert summary["summary"]["buy_matches"] == 2
    assert summary["summary"]["suggested_sell_orders"] == 1
    assert any("风险门控未启用" in w for w in summary["warnings"])

    rows = _signals_rows(env, task_id)
    kinds = {(r["signal_kind"], r["ts_code"]) for r in rows}
    assert ("BUY", "000001.SZ") in kinds
    assert ("BUY", "000002.SZ") in kinds
    assert ("HOLDING", "600519.SH") in kinds
    # SELL_ALL 订单：100 股（含零股语义：这里整百）+ notional
    sell = next(r for r in rows if r["ts_code"] == "600519.SH")
    assert sell["order_status"] == "ELIGIBLE"
    assert float(sell["shares"]) == 100.0
    # BUY 订单：风险手数 100000×1%/(10-9)=1000 股，三价落列
    buy = next(r for r in rows if r["ts_code"] == "000001.SZ")
    assert buy["order_status"] == "ELIGIBLE"
    assert float(buy["shares"]) == 1000.0
    assert float(buy["entry_price"]) == 10.0
    assert float(buy["stop_loss"]) == 9.0
    assert float(buy["take_profit"]) == 12.0
    # 估值价 = 本票最新收盘价（000001 close=10.0，000002 close=11.0，600519 close=12.0）
    assert float(buy["valuation_price"]) == 10.0
    buy2 = next(r for r in rows if r["ts_code"] == "000002.SZ")
    assert float(buy2["valuation_price"]) == 11.0
    sell = next(r for r in rows if r["ts_code"] == "600519.SH")
    assert float(sell["valuation_price"]) == 12.0


def test_snapshot_hash_mismatch_fatal(env):
    task_id = _make_task(env)
    snapshot = _snapshot(STRATEGY, [])
    snapshot["strategy"]["source_hash"] = "0" * 64
    with env["session_factory"]() as session:
        service = QuantExecutionService(
            task_id=task_id, attempt_no=1, snapshot=snapshot,
            market_conn=_market_conn(), session=session,
            execution_control=_control(task_id), effective_trade_date=EFFECTIVE,
        )
        with pytest.raises(StrategySnapshotInvalidError) as exc_info:
            service.run()
        assert exc_info.value.code == "STRATEGY_SNAPSHOT_INVALID"


def test_cancel_before_first_batch_raises_and_no_signals(env):
    task_id = _make_task(env)
    snapshot = _snapshot(STRATEGY, [])
    with env["session_factory"]() as session:
        service = QuantExecutionService(
            task_id=task_id, attempt_no=1, snapshot=snapshot,
            market_conn=_market_conn(), session=session,
            execution_control=_control(task_id, cancelled=True), effective_trade_date=EFFECTIVE,
        )
        with pytest.raises(ExecutionInactiveError):
            service.run()
    assert _signals_rows(env, task_id) == []


def test_data_unavailable_counted_not_blocking(env):
    task_id = _make_task(env)
    snapshot = _snapshot(STRATEGY, [])
    conn = _market_conn(days={"000002.SZ": 100})  # 000002 只有 100 根 → DATA_UNAVAILABLE
    with env["session_factory"]() as session:
        service = QuantExecutionService(
            task_id=task_id, attempt_no=1, snapshot=snapshot,
            market_conn=conn, session=session,
            execution_control=_control(task_id), effective_trade_date=EFFECTIVE,
        )
        summary = service.run()
    assert summary["summary"]["data_complete"] == 2
    assert summary["summary"]["failed_count"] == 1
    rows = _signals_rows(env, task_id)
    assert {r["ts_code"] for r in rows} == {"000001.SZ", "600519.SH"}
