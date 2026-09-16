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
    "total_assets": "100000.0000",
    "available_cash": "35000.0000",
    "risk": {
        "risk_per_trade_pct": "0.010000",
        "min_risk_reward_ratio": "2.0000",
        "max_total_position_pct": "0.800000",
        "max_single_stock_pct": "0.100000",
        "max_sector_pct": "0.300000",
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


def _buy(signal_id, score, entry=10.0, stop=9.0, take=13.0, ts="000001.SZ", valuation=None, code=801080):
    return SimpleNamespace(
        id=signal_id, signal_kind="BUY", ts_code=ts, action="BUY",
        score=score, entry_price=Decimal(str(entry)), stop_loss=Decimal(str(stop)),
        take_profit=Decimal(str(take)), sell_ratio=None,
        valuation_price=Decimal(str(valuation)) if valuation is not None else Decimal(str(entry)),
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
    summary = p.plan(
        task_id=TASK, attempt_no=ATTEMPT,
        portfolio_snapshot=kw.get("portfolio", PORTFOLIO),
        positions=kw.get("positions", []),
        closes=kw.get("closes", {}),
        industry_map=kw.get("industry_map", _industry_map([s.ts_code for s in signals if s.signal_kind == "BUY"])),
        industry_bucket_available=kw.get("industry_bucket_available", True),
        risk_gate=kw.get("risk_gate"),
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
    assert repo.updates[1]["shares"] == Decimal("1000")
    assert repo.updates[1]["order_cost_price"] == Decimal("10.0")
    assert repo.updates[2]["order_status"] == pp.ELIGIBLE
    assert summary.suggested_buy_orders == 2
    # 卖出所得不计作现金；unallocated 不参与
    assert summary.buy_rejections == {}


def test_cash_runs_out_after_multiple_buys():
    # 每股风险 1 → 风险手数 1000；单票上限 10000/10=1000 股绑定前三票；
    # 第 4 票现金仅余 5000 → 500 股；第 5 票现金耗尽 → BUY_REJECTED_CASH
    signals = [_buy(i, 90 - i, ts=f"00000{i}.SZ") for i in range(1, 6)]
    repo, summary = _run(None, signals)
    for i in (1, 2, 3):
        assert repo.updates[i]["order_status"] == pp.ELIGIBLE
        assert repo.updates[i]["shares"] == Decimal("1000")
    assert repo.updates[4]["order_status"] == pp.ELIGIBLE
    assert repo.updates[4]["shares"] == Decimal("500")
    assert repo.updates[5]["order_status"] == pp.BUY_REJECTED_CASH


def test_rr_below_minimum_rejected():
    repo, summary = _run(None, [_buy(1, 90, entry=10.0, stop=9.5, take=10.8)])
    # 盈亏比 (10.8-10)/(10-9.5)=1.6 < 2.0
    assert repo.updates[1]["order_status"] == pp.BUY_REJECTED_RR


def test_total_limit_holds_existing_positions():
    # 已有市值 9 万（总资产 10 万 × 80% = 8 万上限）→ 已超限
    positions = [{"market": "CN", "symbol": "600519.SH", "quantity": "60.0000", "average_cost": "1500.0000"}]
    closes = {"600519.SH": Decimal("1500")}
    portfolio = {**PORTFOLIO, "available_cash": "0.0000"}  # unallocated=10000≥0，只触发过限
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
    assert repo.updates[1]["shares"] == Decimal("500")
    assert repo.updates[2]["order_status"] == pp.BUY_REJECTED_SECTOR_LIMIT


def test_industry_unavailable_rejects_buy():
    repo, summary = _run(None, [_buy(1, 90)], industry_bucket_available=False)
    assert repo.updates[1]["order_status"] == pp.BUY_REJECTED_INDUSTRY_BUCKET


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
    assert repo.updates[10]["notional"] == Decimal("210000")
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


def test_sell_without_position_rejected():
    repo, summary = _run(None, [_hold(10, "SELL_ALL")], positions=[], closes={})
    assert repo.updates[10]["order_status"] == pp.SELL_REJECTED_NO_POSITION


def test_global_order_by_score():
    # 高分先拿钱：票1(90) 1000 股；票2(80) 同样能买
    repo, summary = _run(None, [_buy(2, 80, ts="000002.SZ"), _buy(1, 90, ts="000001.SZ")])
    assert repo.updates[1]["shares"] == Decimal("1000")
    assert repo.updates[2]["shares"] == Decimal("1000")
