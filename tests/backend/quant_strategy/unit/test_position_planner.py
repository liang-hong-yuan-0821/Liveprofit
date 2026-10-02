# test-catalog-begin
# {
#   "purpose": "PositionPlanner 单测（plan 4.3.1/4.3.3）：资金/盈亏比/行业/整手裁剪与 SELL 规则。",
#   "keywords": [
#     "量化策略",
#     "执行准入",
#     "现金",
#     "订单",
#     "规划器",
#     "投资组合",
#     "仓位管理",
#     "数量",
#     "风险",
#     "板块分析",
#     "复用",
#     "状态",
#     "个股分析",
#     "position_planner",
#     "admission",
#     "cash",
#     "order",
#     "planner",
#     "portfolio",
#     "position",
#     "quantity",
#     "risk",
#     "sector",
#     "share",
#     "status",
#     "stock"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/application/position_planner.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""PositionPlanner 单测（plan 4.3.1/4.3.3）：资金/盈亏比/行业/整手裁剪与 SELL 规则。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from backend.modules.quant_strategy.application import position_planner as pp
from backend.modules.quant_strategy.application.position_planner import PositionPlanner

TASK = uuid.uuid4()
ATTEMPT = 1

PORTFOLIO = {
    "name": "核心仓",
    "version": 1,
    "risk_profile": "AGGRESSIVE",
    "total_assets": "100000.0000",
    "available_cash": "35000.0000",
    "risk": {
        "risk_per_trade_pct": "0.007500",
        "min_risk_reward_ratio": "2.0000",
        "max_total_position_pct": "0.900000",
        "max_single_stock_pct": "0.100000",
        "max_sector_pct": "0.300000",
        "max_portfolio_open_risk_pct": "0.060000",
        "max_sector_open_risk_pct": "0.030000",
        "max_daily_new_risk_pct": "0.030000",
        "max_drawdown_pct": "0.250000",
        "max_daily_loss_pct": "0.100000",
        "net_asset_value": "100000.0000",
        "peak_net_asset_value": "100000.0000",
        "day_start_net_asset_value": "100000.0000",
        "risk_facts_as_of": "2026-09-18",
    },
}

INDUSTRY = {"801080": {"industry_code": "801080", "name": "电子"}}


class FakeSignalsRepo:
    def __init__(self, signals: list):
        self._signals = list(signals)
        self.updates: dict[int, dict] = {}

    def list_actionable(self, task_id, attempt_no):
        return iter(self._signals)

    def update_order_fields(self, signal_id: int, values: dict) -> None:
        self.updates[signal_id] = values


def _buy(signal_id, score, entry=10.0, stop=9.0, take=13.0, ts="000001.SZ", valuation=None, code=801080, market=None):
    if market is None:
        raw = valuation if valuation is not None else entry
        market = {"trade_date": "2026-09-18", "raw_close": str(raw),
                  "qfq_close": str(entry), "adv20_amount": "100000",
                  "up_limit": str(Decimal(str(raw)) * Decimal("1.1")),
                  "is_suspended": False, "is_st": False}
    return SimpleNamespace(
        id=signal_id, signal_kind="BUY", ts_code=ts, action="BUY",
        score=score, entry_price=Decimal(str(entry)), stop_loss=Decimal(str(stop)),
        take_profit=Decimal(str(take)), sell_ratio=None,
        valuation_price=Decimal(str(valuation)) if valuation is not None else Decimal(str(entry)),
        execution_market=market,
        order_status=None, notional=None,
    )


def _hold(signal_id, action, ts="600519.SH", ratio=None):
    return SimpleNamespace(
        id=signal_id, signal_kind="HOLDING", ts_code=ts, action=action,
        score=Decimal("50"), entry_price=None, stop_loss=None, take_profit=None,
        sell_ratio=Decimal(str(ratio)) if ratio is not None else None,
        valuation_price=None, order_status=None, notional=None,
    )


def _industry_map(ts_codes: list[str]):
    # 默认每票独立行业桶（行业上限由 test_sector_limit_and_unknown_industry 显式覆盖）
    return {ts: {"industry_code": f"IND{i}", "name": f"行业{i}"} for i, ts in enumerate(ts_codes)}


def _run(planner, signals, **kw):
    repo = FakeSignalsRepo(signals)
    p = PositionPlanner(repo)
    positions = [
        {**position, "active_stop_price": position.get("active_stop_price", position["average_cost"])}
        for position in kw.get("positions", [])
    ]
    for signal in signals:
        if signal.signal_kind == "HOLDING" and not getattr(signal, "execution_market", None):
            holding = next((item for item in positions if item["symbol"] == signal.ts_code), None)
            price = kw.get("closes", {}).get(signal.ts_code, holding["average_cost"] if holding else "10")
            signal.execution_market = {"trade_date": "2026-09-18", "raw_close": price,
                                       "is_suspended": False, "down_limit": Decimal(str(price)) * Decimal("0.9")}
    summary = p.plan(
        task_id=TASK, attempt_no=ATTEMPT,
        portfolio_snapshot=kw.get("portfolio", PORTFOLIO),
        positions=positions,
        closes=kw.get("closes", {}),
        industry_map=kw.get("industry_map", _industry_map([s.ts_code for s in signals if s.signal_kind == "BUY"])),
        industry_bucket_available=kw.get("industry_bucket_available", True),
        risk_gate=kw.get("risk_gate"),
        pending_orders=kw.get("pending_orders"),
        admission_block_code=kw.get("admission_block_code"),
    )
    return repo, summary


def test_risk_shares_and_cash_cap():
    # 每股风险 1 元 → 风险手数 = 100000×1% / 1 = 1000 股；现金 35000/10 → 3500 股
    # → 取 min=1000 股；第二只票现金只剩 35000-10000=25000 → 2500 股（风险手数 1000 更小）
    repo, summary = _run(
        None,
        [_buy(1, 90, ts="000001.SZ"), _buy(2, 80, ts="000002.SZ")],
    )
    assert repo.updates[1]["order_status"] == pp.ELIGIBLE
    assert repo.updates[1]["shares"] == Decimal("700")
    assert repo.updates[1]["order_cost_price"] == Decimal("10.01")
    assert repo.updates[1]["order_stop_price"] == Decimal("9.0")
    assert repo.updates[1]["estimated_fees"] == Decimal("5.08")
    assert repo.updates[2]["order_status"] == pp.ELIGIBLE
    assert summary.suggested_buy_orders == 2
    # 卖出所得不计作现金；unallocated 不参与
    assert summary.buy_rejections == {}


def test_cash_runs_out_after_multiple_buys():
    # 两笔700股连同费用后，1.5万元现金不足第三笔最低100股。
    signals = [_buy(i, 90 - i, ts=f"00000{i}.SZ") for i in range(1, 4)]
    repo, summary = _run(None, signals, portfolio={**PORTFOLIO, "available_cash": "15000"})
    for i in (1, 2):
        assert repo.updates[i]["order_status"] == pp.ELIGIBLE
        assert repo.updates[i]["shares"] == Decimal("700")
    assert repo.updates[3]["order_status"] == pp.BUY_REJECTED_CASH


def test_rr_below_minimum_rejected():
    repo, summary = _run(None, [_buy(1, 90, entry=10.0, stop=9.5, take=10.8)])
    # 盈亏比 (10.8-10)/(10-9.5)=1.6 < 2.0
    assert repo.updates[1]["order_status"] == pp.BUY_REJECTED_RR


def test_gap_up_order_cost_rechecks_price_range_and_rr():
    outside = _buy(1, 90, entry=10, stop=9, take=13, valuation=12.1)
    outside.entry_lower, outside.entry_upper = Decimal("9"), Decimal("11")
    repo, _ = _run(None, [outside])
    assert repo.updates[1]["order_status"] == pp.BUY_REJECTED_PRICE_RANGE

    repo2, _ = _run(None, [_buy(2, 90, entry=10, stop=9, take=12.001, valuation=11.5)])
    # 信号盈亏比刚高于2，映射后的不利滑点使成本盈亏比跌破2。
    assert repo2.updates[2]["order_status"] == pp.BUY_REJECTED_RR


def test_total_limit_holds_existing_positions():
    # 已有市值 9 万（总资产 10 万 × 80% = 8 万上限）→ 已超限
    positions = [{"market": "CN", "symbol": "600519.SH", "quantity": "60.0000", "average_cost": "1500.0000"}]
    closes = {"600519.SH": Decimal("1500")}
    portfolio = {**PORTFOLIO, "available_cash": "0.0000",
                 "risk": {**PORTFOLIO["risk"], "max_total_position_pct": "0.8"}}  # unallocated=10000≥0，只触发过限
    repo, summary = _run(None, [_buy(1, 90)], positions=positions, closes=closes, portfolio=portfolio)
    assert summary.buy_rejections.get(pp.PORTFOLIO_ALREADY_OVER_LIMIT) == 1
    assert repo.updates[1]["order_status"] == pp.PORTFOLIO_ALREADY_OVER_LIMIT


def test_single_stock_limit():
    # 持仓 5 万 → 单票上限 10%×10 万=1 万 → 已超 → 该票 BUY 被单票上限拒绝
    positions = [{"market": "CN", "symbol": "000001.SZ", "quantity": "5000.0000", "average_cost": "10.0000"}]
    closes = {"000001.SZ": Decimal("10")}
    repo, summary = _run(None, [_buy(1, 90, ts="000001.SZ")], positions=positions, closes=closes)
    assert repo.updates[1]["order_status"] == pp.BUY_REJECTED_SINGLE_STOCK_LIMIT


def test_sector_limit_and_unknown_industry():
    # 行业桶已有 2.5 万持仓市值；上限 3 万 → 第一笔 1 万可过，第二笔被行业上限拒
    positions = [
        {"market": "CN", "symbol": "600519.SH", "quantity": "2500.0000", "average_cost": "10.0000"},
    ]
    closes = {"600519.SH": Decimal("10")}
    industry_map = {
        "600519.SH": INDUSTRY["801080"],
        "000001.SZ": INDUSTRY["801080"],
        "000002.SZ": INDUSTRY["801080"],
    }
    repo, summary = _run(
        None,
        [_buy(1, 90, ts="000001.SZ"), _buy(2, 80, ts="000002.SZ")],
        positions=positions, closes=closes, industry_map=industry_map,
    )
    assert repo.updates[1]["order_status"] == pp.ELIGIBLE  # 1000 股×10 = 1 万 → 行业余量 3万-2.5万-1万<0？
    # 行业余量 = 3 万 - 2.5 万 = 5000 → 第一笔也只能买 500 股
    assert repo.updates[1]["shares"] == Decimal("400")
    assert repo.updates[2]["order_status"] == pp.BUY_REJECTED_SECTOR_LIMIT


def test_industry_unavailable_rejects_buy():
    repo, summary = _run(None, [_buy(1, 90)], industry_bucket_available=False)
    assert repo.updates[1]["order_status"] == pp.BUY_REJECTED_INDUSTRY_BUCKET
    assert repo.updates[1]["order_entry_price"] == Decimal("10.01")
    assert repo.updates[1]["execution_policy_version"] == "cn_execution_v1"


def test_trade_status_rejection_precedes_portfolio_constraints():
    market = {
        "trade_date": "2026-09-18", "qfq_close": 10, "raw_close": 10,
        "raw_amount": 100000, "is_suspended": True, "is_st": False,
        "up_limit": 11, "down_limit": 9, "market_board": "MAIN",
    }
    repo, _ = _run(None, [_buy(1, 90, market=market)], industry_bucket_available=False)
    assert repo.updates[1]["order_status"] == "BUY_REJECTED_SUSPENDED"
    assert repo.updates[1]["earliest_execution_trade_date"].isoformat() == "2026-09-21"


def test_liquidity_participation_caps_final_lot_size():
    market = {
        "trade_date": "2026-09-18", "qfq_close": 10, "raw_close": 10,
        "raw_amount": 100000, "adv20_amount": 50, "is_suspended": False, "is_st": False,
        "up_limit": 11, "down_limit": 9, "market_board": "CHINEXT",
    }
    repo, _ = _run(None, [_buy(1, 90, market=market)])
    # 5 万元成交额 × 5% / 10.01，最终只能建议 200 股。
    assert repo.updates[1]["shares"] == Decimal("200")


def test_unknown_holding_industry_blocks_new_buy_but_keeps_partial_sell():
    positions = [{"symbol": "600519.SH", "quantity": "500", "average_cost": "10"}]
    repo, summary = _run(
        None, [_buy(1, 90), _hold(2, "SELL_PARTIAL", ratio=0.5)],
        positions=positions, closes={"600519.SH": Decimal("10")},
    )
    assert repo.updates[1]["order_status"] == pp.BUY_REJECTED_INDUSTRY_BUCKET
    assert repo.updates[2]["shares"] == Decimal("200")
    assert summary.suggested_buy_orders == 0
    assert summary.suggested_sell_orders == 1


def test_unallocated_negative_blocks_all_buys():
    # total 10 万、cash 3.5 万、持仓市值 7 万 → unallocated = -5000
    positions = [{"market": "CN", "symbol": "600519.SH", "quantity": "100.0000", "average_cost": "700.0000"}]
    closes = {"600519.SH": Decimal("700")}
    repo, summary = _run(None, [_buy(1, 90, ts="000001.SZ"), _buy(2, 80, ts="000002.SZ")], positions=positions, closes=closes)
    assert repo.updates[1]["order_status"] == pp.PORTFOLIO_VALUE_INCONSISTENT
    assert repo.updates[2]["order_status"] == pp.PORTFOLIO_VALUE_INCONSISTENT


def test_stale_position_valuation_warns_and_blocks_buys():
    positions = [{"market": "CN", "symbol": "600519.SH", "quantity": "10.0000", "average_cost": "1500.0000"}]
    repo, summary = _run(None, [_buy(1, 90)], positions=positions, closes={})
    assert pp.STALE_POSITION_VALUATION in summary.warnings
    assert repo.updates[1]["order_status"] == pp.BUY_REJECTED_STALE_VALUATION


def test_sell_all_includes_odd_lots():
    positions = [{"market": "CN", "symbol": "600519.SH", "quantity": "150.0000", "average_cost": "1500.0000"}]
    closes = {"600519.SH": Decimal("1400")}
    repo, summary = _run(None, [_hold(10, "SELL_ALL")], positions=positions, closes=closes)
    assert repo.updates[10]["order_status"] == pp.ELIGIBLE
    assert repo.updates[10]["shares"] == Decimal("150")  # 含零股
    assert repo.updates[10]["notional"] == Decimal("209790.000000")
    assert repo.updates[10]["estimated_slippage"] == Decimal("210.000000")
    assert summary.suggested_sell_orders == 1


def test_sell_partial_lot_rules():
    positions = [{"market": "CN", "symbol": "600519.SH", "quantity": "500.0000", "average_cost": "10.0000"}]
    closes = {"600519.SH": Decimal("10")}
    # 500×0.5=250 → 整手 200
    repo, summary = _run(None, [_hold(10, "SELL_PARTIAL", ratio=0.5)], positions=positions, closes=closes)
    assert repo.updates[10]["shares"] == Decimal("200")
    # 500×0.1=50 → 不足一手
    repo2, summary2 = _run(None, [_hold(11, "SELL_PARTIAL", ratio=0.1)], positions=positions, closes=closes)
    assert repo2.updates[11]["order_status"] == pp.SELL_PARTIAL_REJECTED_LOT_SIZE


def test_two_sell_sources_in_one_plan_share_reserved_quantity():
    positions = [{"market": "CN", "symbol": "600519.SH", "quantity": "1000", "average_cost": "10"}]
    repo, summary = _run(None, [_hold(701, "SELL_PARTIAL", ratio=0.6), _hold(702, "SELL_ALL")],
                         positions=positions, closes={"600519.SH": Decimal(10)})
    assert repo.updates[701]["shares"] == 600
    assert repo.updates[702]["shares"] == 400
    assert summary.suggested_sell_orders == 2


def test_sell_without_position_rejected():
    repo, summary = _run(None, [_hold(10, "SELL_ALL")], positions=[], closes={})
    assert repo.updates[10]["order_status"] == pp.SELL_REJECTED_NO_POSITION


def test_global_order_by_score():
    # 高分先拿钱：票1(90) 1000 股；票2(80) 同样能买
    repo, summary = _run(None, [_buy(2, 80, ts="000002.SZ"), _buy(1, 90, ts="000001.SZ")])
    assert repo.updates[1]["shares"] == Decimal("700")
    assert repo.updates[2]["shares"] == Decimal("700")


def test_open_risk_is_recomputed_after_each_accepted_buy():
    portfolio = {
        **PORTFOLIO,
        "risk": {
            **PORTFOLIO["risk"],
            "max_portfolio_open_risk_pct": "0.012",
            "max_sector_open_risk_pct": "0.03",
            "max_daily_new_risk_pct": "0.03",
        },
    }
    repo, summary = _run(
        None,
        [_buy(1, 90, ts="000001.SZ"), _buy(2, 80, ts="000002.SZ")],
        portfolio=portfolio,
    )
    assert repo.updates[1]["shares"] == Decimal("700")
    assert repo.updates[2]["shares"] == Decimal("400")
    assert summary.portfolio_open_risk == Decimal("1111.00")


def test_equity_circuit_breaker_blocks_buy_but_not_risk_reducing_sell():
    portfolio = {
        **PORTFOLIO,
        "risk": {**PORTFOLIO["risk"], "net_asset_value": "79000"},
    }
    positions = [{
        "symbol": "600519.SH", "quantity": "500", "average_cost": "10",
        "active_stop_price": "9", "available_quantity": "500",
    }]
    industry_map = {
        "600519.SH": {"industry_code": "I0", "name": "消费"},
        "000001.SZ": {"industry_code": "I1", "name": "银行"},
    }
    repo, summary = _run(
        None,
        [_buy(1, 90), _hold(2, "SELL_ALL")],
        portfolio=portfolio,
        positions=positions,
        closes={"600519.SH": Decimal("10")},
        industry_map=industry_map,
    )
    assert repo.updates[1]["order_status"] == "BUY_REJECTED_RISK_CIRCUIT"
    assert repo.updates[2]["order_status"] == pp.ELIGIBLE


def test_same_sector_open_risk_pressure_caps_second_buy():
    portfolio = {
        **PORTFOLIO,
        "risk": {
            **PORTFOLIO["risk"],
            "max_portfolio_open_risk_pct": "0.06",
            "max_sector_open_risk_pct": "0.012",
            "max_daily_new_risk_pct": "0.03",
        },
    }
    industry_map = {
        "000001.SZ": {"industry_code": "I1", "name": "同一行业"},
        "000002.SZ": {"industry_code": "I1", "name": "同一行业"},
    }
    repo, _ = _run(
        None, [_buy(1, 90, ts="000001.SZ"), _buy(2, 80, ts="000002.SZ")],
        portfolio=portfolio, industry_map=industry_map,
    )
    assert repo.updates[1]["shares"] == Decimal("700")
    assert repo.updates[2]["shares"] == Decimal("400")


def test_pending_orders_reserve_buy_cash_and_sell_quantity():
    buy_pending = [{
        "side": "BUY", "symbol": "000003.SZ", "remaining_quantity": "1000",
        "order_entry_price": "10", "order_stop_price": "9", "industry_code": "I9",
        "reserved_cash": "30000",
    }]
    repo, _ = _run(None, [_buy(1, 90)], pending_orders=buy_pending)
    assert repo.updates[1]["shares"] == Decimal("400")

    positions = [{
        "symbol": "600519.SH", "quantity": "500", "average_cost": "10",
        "active_stop_price": "9", "available_quantity": "500",
    }]
    sell_pending = [{"side": "SELL", "symbol": "600519.SH", "remaining_quantity": "300"}]
    repo2, _ = _run(
        None, [_hold(2, "SELL_ALL")], positions=positions,
        closes={"600519.SH": Decimal("10")},
        industry_map={"600519.SH": {"industry_code": "I0", "name": "消费"}},
        pending_orders=sell_pending,
    )
    assert repo2.updates[2]["shares"] == Decimal("200")


def test_admission_denies_new_buy_but_preserves_holding_exit():
    repo, summary = _run(None, [_buy(1, 90), _hold(2, "SELL", ratio=1)],
        admission_block_code="ADMISSION_SUSPENDED",
        positions=[{"market": "CN", "symbol": "600519.SH", "quantity": 100,
                    "average_cost": "10", "available_sell_quantity": 100}],
        closes={"600519.SH": Decimal("10")})
    assert repo.updates[1]["order_status"] == "BUY_REJECTED_ADMISSION"
    assert repo.updates[2]["order_status"] == "ELIGIBLE"
    assert summary.suggested_buy_orders == 0
    assert summary.suggested_sell_orders == 1
    assert "ADMISSION_SUSPENDED" in summary.warnings
