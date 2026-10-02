# test-catalog-begin
# {
#   "purpose": "QuantExecutionService 集成测试（plan 4.3.3）：实时扫描/信号落库/订单回写/取消/快照校验。",
#   "keywords": [
#     "量化策略",
#     "批次",
#     "执行",
#     "复权因子",
#     "流程",
#     "交易意图",
#     "持仓生命周期",
#     "市场分析",
#     "订单",
#     "交易信号",
#     "execution",
#     "batch",
#     "factor",
#     "flow",
#     "intent",
#     "lifecycle",
#     "market",
#     "order",
#     "signal",
#     "signals"
#   ],
#   "covers": [
#     "backend/modules/analysis/infrastructure/execution_control.py",
#     "backend/modules/analysis/infrastructure/quant_execution_market_data.py",
#     "backend/modules/investment_workspace/infrastructure/models.py",
#     "backend/modules/market_data/infrastructure/calendar_adapter.py",
#     "backend/modules/quant_strategy/application/data_readiness.py",
#     "backend/modules/quant_strategy/application/execution.py",
#     "backend/modules/quant_strategy/application/lifecycle_service.py",
#     "backend/modules/quant_strategy/domain/templates.py",
#     "backend/modules/quant_strategy/infrastructure/admission_models.py",
#     "backend/modules/quant_strategy/infrastructure/instrument_rule_certificates.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_models.py",
#     "backend/modules/quant_strategy/infrastructure/models.py",
#     "backend/modules/quant_strategy/infrastructure/signals.py",
#     "db/instrument/dao/ingest_state.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""QuantExecutionService 集成测试（plan 4.3.3）：实时扫描/信号落库/订单回写/取消/快照校验。"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import base64
import json
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from backend.modules.analysis.infrastructure.execution_control import (
    ExecutionControl,
    ExecutionInactiveError,
)
from backend.modules.investment_workspace.infrastructure.models import (
    Portfolio,
    PortfolioPosition,
)
from backend.modules.quant_strategy.application import execution as execution_module
from backend.modules.quant_strategy.application.data_readiness import (
    DataReadinessError,
    DataReadinessSnapshot,
)
from backend.modules.quant_strategy.application.execution import (
    QuantExecutionService,
    StrategySnapshotInvalidError,
)
from backend.modules.quant_strategy.application.lifecycle_service import (
    LifecyclePolicyService,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    PositionLifecycleState,
    SuggestedOrder,
)
from backend.modules.quant_strategy.infrastructure.models import (
    QuantStrategy,
    QuantStrategyVersion,
)
from backend.modules.quant_strategy.infrastructure.signals import QuantExecutionSignal
from db.instrument.dao import ingest_state as state_dao

EFFECTIVE = date(2026, 9, 15)


@pytest.fixture(autouse=True)
def fixed_complete_readiness(monkeypatch):
    """Execution tests use a fake market SQL adapter; readiness has its own SQL tests."""
    def resolve(conn, requested):
        return DataReadinessSnapshot(
            requested_trade_date=requested, market_as_of_trade_date=requested,
            daily_trade_date=requested, factor_trade_date=requested,
            adj_factor_trade_date=requested, trade_status_trade_date=requested,
            coverage_digest="fixture", universe_digest="fixture",
        )

    monkeypatch.setattr(execution_module.DataReadinessGate, "resolve", resolve)


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
                "stop_loss": round(price * 0.9, 2), "take_profit": round(price * 1.3, 2),
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
        adj_rows: list | None = None,
        status_rows: list | None = None,
    ) -> None:
        self.instruments = instruments
        self.daily_rows = daily_rows
        self.factor_rows = factor_rows
        self.industry_rows = industry_rows
        self.ingest_state_rows = ingest_state_rows or []
        self.closes = closes or {}
        self.adj_rows = adj_rows if adj_rows is not None else [
            (r[0], r[1], 1.0) for r in daily_rows
        ]
        latest_by_code = {}
        for r in daily_rows:
            if r[0] not in latest_by_code or r[1] > latest_by_code[r[0]]:
                latest_by_code[r[0]] = r[1]
        self.status_rows = status_rows if status_rows is not None else [
            (ts, d, False, False, close * 1.1 if close is not None else None,
             close * 0.9 if close is not None else None)
            for ts, d in latest_by_code.items()
            for close in [self.closes.get(ts, next((x[5] for x in daily_rows if x[0] == ts), None))]
        ]

    def execute(self, sql: str, params=None):
        if "quant_data_readiness" in sql:
            dates = [r[1] for r in self.daily_rows]
            latest = max(dates) if dates else None
            return _Rows([(latest, latest, latest, latest)])
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
        if "market.adj_factor" in sql:
            return _Rows(self.adj_rows)
        if "market.trade_status_effective" in sql:
            wanted = str(params[1])[:10] if params else None
            return _Rows([r for r in self.status_rows if str(r[1])[:10] == wanted])
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
        rows.append((
            ts_code, d,
            9.5, 9.8, 9.0,
            9.7, 10.5, 8.9,
            0.2, 0.1, 0.1, 55.0,
        ))
        d -= timedelta(days=1)
    return rows


def _snapshot(source_code: str, positions: list[dict]) -> dict:
    frozen_positions = [
        {
            **position,
            "active_stop_price": position.get(
                "active_stop_price", str(float(position["average_cost"]) * 0.9),
            ),
        }
        for position in positions
    ]
    return {
        "schema_version": "quant_execution_snapshot_v2",
        "strategy": {
            "strategy_id": str(uuid.uuid4()),
            "version_id": None,  # explicit lifecycle tests bind a real persisted version
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
                "max_portfolio_open_risk_pct": "0.500000",
                "max_sector_open_risk_pct": "0.500000",
                "max_daily_new_risk_pct": "0.500000",
                "max_drawdown_pct": "0.200000",
                "max_daily_loss_pct": "0.100000",
                "net_asset_value": "100000.0000",
                "peak_net_asset_value": "100000.0000",
                "day_start_net_asset_value": "100000.0000",
                "risk_facts_as_of": EFFECTIVE.isoformat(),
            },
        },
        "positions": frozen_positions,
        "pending_orders": [],
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


def _market_conn(extra_inst: list[str] = ("000001.SZ", "000002.SZ", "600519.SH"),
                 days: dict | None = None, start: date = EFFECTIVE,
                 exchange_days: bool = False):
    instruments = list(extra_inst)
    days = days or {}
    daily = []
    factor = []
    # 默认 300 根（>250 检出「取最老窗口」类回归）且 close 按票递增（检出 valuation_price 串票）
    for idx, ts in enumerate(instruments):
        close = 10.0 + idx
        count = days.get(ts, 300)
        bars = _bars(ts, count, close, start=start)
        factors = _factors(ts, count, start=start)
        if exchange_days:
            from backend.modules.market_data.infrastructure.calendar_adapter import MarketCalendarAdapter
            dates = [s.trade_date for s in MarketCalendarAdapter().schedule("CN").sessions
                     if s.trade_date <= start][-count:][::-1]
            assert len(dates) == count
            bars = [(row[0], day, *row[2:]) for row, day in zip(bars, dates)]
            factors = [(row[0], day, *row[2:]) for row, day in zip(factors, dates)]
        daily += bars
        factor += factors
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


def test_unqualified_full_flow_defers_sell_without_sellable_fact_and_rejects_new_buy(env):
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
    assert summary["summary"]["suggested_sell_orders"] == 0
    assert any("组合开放风险与熔断已启用" in w for w in summary["warnings"])

    rows = _signals_rows(env, task_id)
    kinds = {(r["signal_kind"], r["ts_code"]) for r in rows}
    assert ("BUY", "000001.SZ") in kinds
    assert ("BUY", "000002.SZ") in kinds
    assert ("HOLDING", "600519.SH") in kinds
    # The persisted balance alone cannot certify broker sellable shares.
    sell = next(r for r in rows if r["ts_code"] == "600519.SH")
    assert sell["order_status"] == "SELLABLE_QUANTITY_UNKNOWN"
    # No scoped qualification exists for this frozen strategy snapshot.
    buy = next(r for r in rows if r["ts_code"] == "000001.SZ")
    assert buy["order_status"] == "BUY_REJECTED_ADMISSION"
    assert float(buy["entry_price"]) == 10.0
    assert float(buy["stop_loss"]) == 9.0
    assert float(buy["take_profit"]) == 13.0
    # 估值价 = 本票最新收盘价（000001 close=10.0，000002 close=11.0，600519 close=12.0）
    assert float(buy["valuation_price"]) == 10.0
    buy2 = next(r for r in rows if r["ts_code"] == "000002.SZ")
    assert float(buy2["valuation_price"]) == 11.0
    sell = next(r for r in rows if r["ts_code"] == "600519.SH")
    assert float(sell["valuation_price"]) == 12.0


def test_market_gap_keeps_sell_intent_pending_without_sellable_fact(env, monkeypatch):
    monkeypatch.setattr(
        execution_module.DataReadinessGate, "resolve",
        lambda conn, requested: (_ for _ in ()).throw(DataReadinessError("missing market factors")),
    )
    task_id = _make_task(env)
    positions = [{"market": "CN", "symbol": "600519.SH", "quantity": "100.0000",
                  "average_cost": "10.0000"}]
    with env["session_factory"]() as session:
        service = QuantExecutionService(
            task_id=task_id, attempt_no=1, snapshot=_snapshot(STRATEGY, positions),
            market_conn=_market_conn(), session=session,
            execution_control=_control(task_id), effective_trade_date=EFFECTIVE,
        )
        summary = service.run()
    assert summary["global_data_ready"] is False
    assert summary["market_as_of_trade_date"] is None
    assert summary["summary"]["universe_total"] == 0
    assert summary["summary"]["suggested_sell_orders"] == 0
    rows = _signals_rows(env, task_id)
    assert len(rows) == 1
    assert rows[0]["ts_code"] == "600519.SH"
    assert rows[0]["action"] == "SELL_ALL"
    assert rows[0]["order_status"] == "SELLABLE_QUANTITY_UNKNOWN"


def test_eligible_signal_materializes_one_persisted_suggested_order(env, monkeypatch):
    from backend.modules.quant_strategy.infrastructure.admission_models import StrategyAdmissionEvent
    from backend.modules.quant_strategy.infrastructure.instrument_rule_certificates import InstrumentRuleCertificateRepository
    from tests.backend.quant_strategy.support.instrument_rule_certificates import _signed, KEY

    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    monkeypatch.setenv("LIVEPROFIT_RULE_REVIEW_KEYS", json.dumps({
        "rule-reviewer": base64.b64encode(KEY).decode(),
    }))
    task_id = _make_task(env)
    snapshot = _snapshot(STRATEGY, [])
    snapshot["strategy"]["lifecycle_policy"] = {
        "id": str(uuid.uuid4()), "content_hash": "d" * 64,
        "config": {"template_id": "ma_trend_cross_v1", "initial_exposure_pct": "0.50"},
    }
    portfolio_id = uuid.UUID(snapshot["portfolio"]["id"])
    with env["session_factory"]() as session:
        strategy = QuantStrategy(id=uuid.uuid4(), name=f"scan-{uuid.uuid4().hex[:8]}", version=1)
        session.add(strategy)
        session.flush()
        version = QuantStrategyVersion(
            id=uuid.uuid4(), strategy_id=strategy.id, version_no=1,
            status="PUBLISHED", source_code=STRATEGY,
            source_hash=hashlib.sha256(STRATEGY.encode()).hexdigest(), version=1,
        )
        session.add(version)
        session.flush()
        snapshot["strategy"]["version_id"] = str(version.id)
        snapshot["portfolio"]["risk_profile"] = "BALANCED"
        snapshot["portfolio"]["risk"]["risk_facts_as_of"] = today.isoformat()
        session.add(Portfolio(
            id=portfolio_id, name=f"exec-{uuid.uuid4().hex[:8]}", version=1,
            total_assets=Decimal("100000"), available_cash=Decimal("35000"),
            risk_profile="BALANCED", risk_per_trade_pct=Decimal(".005"),
            min_risk_reward_ratio=Decimal(2), max_total_position_pct=Decimal(".75"),
            max_single_stock_pct=Decimal(".08"), max_sector_pct=Decimal(".8"),
            max_portfolio_open_risk_pct=Decimal(".04"), max_sector_open_risk_pct=Decimal(".02"),
            max_daily_new_risk_pct=Decimal(".02"), max_drawdown_pct=Decimal(".15"),
            net_asset_value=Decimal("100000"), peak_net_asset_value=Decimal("100000"),
            day_start_net_asset_value=Decimal("100000"), risk_facts_as_of=today,
        ))
        session.add(StrategyAdmissionEvent(
            strategy_version_id=version.id, family_id="scan", asset_scope="CN_STOCK",
            risk_profile="BALANCED", revision=1, state="ADVISORY",
            reason="isolated fixture", request_key="fixture",
            recorded_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
            evidence_completed_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
            evidence_ref="test://fixture", evidence_sha256="a" * 64,
            valid_until=datetime(2030, 1, 1, tzinfo=timezone.utc),
            net_expectancy_lower_bound=Decimal(".002"),
        ))
        certificate_id, observation, rule_capture, identity_capture, signature, rule = _signed(
            session, symbol="000001.SZ", step=100)
        InstrumentRuleCertificateRepository(session).issue(
            certificate_id=certificate_id, observation_id=observation.id,
            rule_capture_id=rule_capture.id, identity_capture_id=identity_capture.id,
            reviewer_id="rule-reviewer", interpretation_note="verified fixture",
            identity_symbol=rule.symbol, identity_asset_type="stock",
            identity_locator="fixture identity row 1", review_signature=signature,
        )
        session.commit()

    with env["session_factory"]() as session:
        service = QuantExecutionService(
            task_id=task_id, attempt_no=1, snapshot=snapshot,
            market_conn=_market_conn(extra_inst=["000001.SZ"], start=today, exchange_days=True), session=session,
            execution_control=_control(task_id), effective_trade_date=today,
        )
        execution_result = service.run()
        service._persist_suggested_orders(  # noqa: SLF001 - retry idempotency contract
            snapshot["portfolio"], positions=[],
            industry_map={"000001.SZ": {"industry_code": "801080", "industry_name": "电子"}},
        )
        session.commit()

    with env["session_factory"]() as session:
        orders = session.query(SuggestedOrder).filter_by(portfolio_id=portfolio_id).all()
        assert len(orders) == 1, (execution_result["buy_rejections"],
                                  [(row["order_status"], row["shares"]) for row in _signals_rows(env, task_id)])
        order = orders[0]
        signal = session.get(QuantExecutionSignal, order.source_signal_id)
        assert order.source_signal_id is not None
        assert order.side == "BUY"
        assert order.symbol == "000001.SZ"
        assert order.industry_code == "801080"
        assert order.status == "PROPOSED"
        assert signal.shares >= order.quantity >= Decimal(100)
        assert order.quantity % 100 == 0
        assert order.rule_certificate_id == certificate_id
        assert order.rule_authorized_at <= order.decision_at
        assert order.reserved_cash > 0


def test_arc_bottom_buy_freezes_same_context_neckline_seed(env):
    task_id = _make_task(env)
    snapshot = _snapshot(STRATEGY, [])
    snapshot["strategy"].update({
        "template_id": "arc_bottom_75a_v1",
        "required_bars": 75,
    })
    with env["session_factory"]() as session:
        QuantExecutionService(
            task_id=task_id, attempt_no=1, snapshot=snapshot,
            market_conn=_market_conn(extra_inst=["000001.SZ"]), session=session,
            execution_control=_control(task_id), effective_trade_date=EFFECTIVE,
        ).run()

    with env["session_factory"]() as session:
        signal = session.query(QuantExecutionSignal).filter_by(
            task_id=task_id, ts_code="000001.SZ",
        ).one()
        seed = signal.execution_market["lifecycle_seed"]
        assert Decimal(seed["arc_neckline_price"]) == Decimal("10")
        assert seed["input_hash"]


def test_existing_lifecycle_arbitrates_script_sell_into_one_delta_order(env):
    task_id = _make_task(env)
    with env["session_factory"]() as session:
        policy = LifecyclePolicyService(session).publish(
            policy_key=f"exec-life-{uuid.uuid4().hex[:8]}", required_fields=[],
            config={"template_id": "ma_trend_cross_v1", "reward_multiple": "2.5"},
        )
    portfolio_id, position_id = uuid.uuid4(), uuid.uuid4()
    with env["session_factory"]() as session:
        strategy = QuantStrategy(id=uuid.uuid4(), name=f"exec-life-{uuid.uuid4().hex[:8]}", version=1)
        version = QuantStrategyVersion(
            id=uuid.uuid4(), strategy_id=strategy.id, version_no=1, status="PUBLISHED",
            source_code=STRATEGY, source_hash=hashlib.sha256(STRATEGY.encode()).hexdigest(),
            template_id="ma_trend_cross_v1", lifecycle_policy_version_id=policy.id, version=1,
        )
        portfolio = Portfolio(
            id=portfolio_id, name=f"exec-life-p-{uuid.uuid4().hex[:8]}", version=1,
            total_assets=Decimal("100000"), available_cash=Decimal("35000"),
        )
        position = PortfolioPosition(
            id=position_id, portfolio_id=portfolio_id, market="CN", symbol="600519.SH",
            quantity=Decimal("500"), average_cost=Decimal("10"), active_stop_price=Decimal("9"),
        )
        session.add_all([strategy, portfolio])
        session.flush()
        session.add_all([version, position])
        session.flush()
        lifecycle = PositionLifecycleState(
            id=uuid.uuid4(), portfolio_id=portfolio_id, position_id=position_id,
            market="CN", symbol="600519.SH", strategy_version_id=version.id,
            lifecycle_policy_version_id=policy.id, initial_fill_price=Decimal("10"),
            initial_stop_price=Decimal("9"), risk_capacity_shares=Decimal("1000"),
            target_exposure_pct=Decimal("0.50"), target_shares=Decimal("500"),
            phase="INITIALIZED", profit_take_price=Decimal("12.5"), state_version=1,
        )
        session.add(lifecycle)
        session.commit()

    snapshot = _snapshot(
        STRATEGY,
        [{"market": "CN", "symbol": "600519.SH", "quantity": "500", "average_cost": "10"}],
    )
    snapshot["portfolio"]["id"] = str(portfolio_id)
    snapshot["strategy"].update({
        "version_id": str(version.id), "template_id": "ma_trend_cross_v1", "required_bars": 2,
        "lifecycle_policy": {
            "id": str(policy.id), "content_hash": policy.content_hash, "config": policy.config,
        },
    })
    with env["session_factory"]() as session:
        result = QuantExecutionService(
            task_id=task_id, attempt_no=1, snapshot=snapshot,
            market_conn=_market_conn(), session=session,
            execution_control=_control(task_id), effective_trade_date=EFFECTIVE,
        ).run()
    assert result["summary"]["suggested_sell_orders"] == 0
    with env["session_factory"]() as session:
        orders = session.query(SuggestedOrder).filter_by(
            portfolio_id=portfolio_id, symbol="600519.SH",
        ).all()
        assert orders == []
        signal = session.query(QuantExecutionSignal).filter_by(task_id=task_id, ts_code="600519.SH").one()
        assert signal.order_status == "MANAGED_BY_LIFECYCLE"


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
    assert {r["ts_code"] for r in rows} == {"000001.SZ", "000002.SZ", "600519.SH"}
    error = next(r for r in rows if r["ts_code"] == "000002.SZ")
    assert error["signal_kind"] == "ERROR"
    assert error["error_code"] == "WARMUP_INCOMPLETE"


def test_execution_uses_frozen_template_contract_after_registry_changes(env, monkeypatch):
    from backend.modules.quant_strategy.application import execution as execution_module
    from backend.modules.quant_strategy.domain.templates import (
        TEMPLATES,
        freeze_template_contract,
    )

    task_id = _make_task(env)
    snapshot = _snapshot(STRATEGY, [])
    snapshot["strategy"].update({
        "template_id": "ma_trend_cross_v1",
        "required_bars": 2,
        "template_contract": freeze_template_contract(TEMPLATES["ma_trend_cross_v1"]),
        "name": "冻结显示名",
    })

    def registry_must_not_be_read(_template_id):
        raise AssertionError("新快照执行期不得读取可变模板注册表")

    monkeypatch.setattr(execution_module, "get_template", registry_must_not_be_read)
    with env["session_factory"]() as session:
        result = QuantExecutionService(
            task_id=task_id, attempt_no=1, snapshot=snapshot,
            market_conn=_market_conn(), session=session,
            execution_control=_control(task_id), effective_trade_date=EFFECTIVE,
        ).run()
    assert result["strategy"]["name"] == "冻结显示名"
    assert result["summary"]["scanned"] == 3


def test_loader_aligns_factor_values_by_trade_date_and_rejects_missing_latest():
    from backend.modules.analysis.infrastructure.quant_execution_market_data import (
        MarketContextBatchLoader,
    )

    ts = "000001.SZ"
    daily = _bars(ts, 3, 10.0)
    # 故意乱序并用字符串日期，验证不是按行号、对象类型或整条 bar 对齐。
    def f(day, ma5, ma20, rsi):
        return (ts, day, ma5, ma20, 60.0, 10.0, 11.0, 9.0, 0.2, 0.1, 0.1, rsi)

    factor = [
        f("2026-09-13", 3.0, 30.0, 53.0),
        f("2026-09-15", 1.0, 10.0, 51.0),
        f("2026-09-14", 2.0, 20.0, 52.0),
    ]
    conn = FakeMarketConn([ts], daily, factor, [], closes={ts: 10.0})
    item = MarketContextBatchLoader(lookback=3).load_batch(conn, [ts], EFFECTIVE)[0]
    assert item["status"] == "OK"
    assert item["context"]["ohlcv"]["trade_date"] == ["2026-09-13", "2026-09-14", "2026-09-15"]
    assert item["context"]["indicators"]["ma_bfq_5"] == [3.0, 2.0, 1.0]
    assert item["context"]["indicators"]["ma_bfq_20"] == [30.0, 20.0, 10.0]
    assert item["context"]["indicators"]["rsi_bfq_6"] == [53.0, 52.0, 51.0]

    missing_latest = FakeMarketConn([ts], daily, factor[:1], [], closes={ts: 10.0})
    rejected = MarketContextBatchLoader(lookback=3).load_batch(missing_latest, [ts], EFFECTIVE)[0]
    assert rejected["status"] == "INDICATOR_UNAVAILABLE"

    all_null = FakeMarketConn(
        [ts], daily,
        [(ts, row[1], None, None, None, None, None, None, None, None, None, None) for row in daily],
        [], closes={ts: 10.0},
    )
    rejected_null = MarketContextBatchLoader(lookback=3).load_batch(all_null, [ts], EFFECTIVE)[0]
    assert rejected_null["status"] == "INDICATOR_UNAVAILABLE"
