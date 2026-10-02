# test-catalog-begin
# {
#   "purpose": "第一波模板真实 loader + sandbox runner 与除权 fixture。",
#   "keywords": [
#     "量化策略",
#     "止损",
#     "template_loader_runner",
#     "stop"
#   ],
#   "covers": [
#     "AI/strategy_sandbox/runner.py",
#     "backend/modules/analysis/infrastructure/quant_execution_market_data.py",
#     "backend/modules/quant_strategy/application/execution_constraints.py",
#     "backend/modules/quant_strategy/domain/templates.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""第一波模板真实 loader + sandbox runner 与除权 fixture。"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from AI.strategy_sandbox.runner import run_strategy
from backend.modules.analysis.infrastructure.quant_execution_market_data import MarketContextBatchLoader
from backend.modules.quant_strategy.application.execution_constraints import PriceBasisMapper
from backend.modules.quant_strategy.domain.templates import TEMPLATES, validate_template_context


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _MarketConn:
    def __init__(self, daily, factors, adj, status):
        self.daily, self.factors, self.adj, self.status = daily, factors, adj, status

    def execute(self, sql, params=None):
        upper = str(params[1])[:10] if params else "9999-12-31"
        limit = int(params[2]) if params and len(params) > 2 else 9999
        if "market.instrument_daily" in sql:
            rows = [r for r in self.daily if str(r[1])[:10] <= upper]
        elif "market.factor_daily" in sql:
            rows = [r for r in self.factors if str(r[1])[:10] <= upper]
        elif "market.adj_factor" in sql:
            rows = [r for r in self.adj if str(r[1])[:10] <= upper]
        elif "market.trade_status_effective" in sql:
            rows = [r for r in self.status if str(r[1])[:10] == upper]
        else:
            rows = []
        if "ranked" in sql:
            rows = rows[:limit]
        return _Rows(rows)


def _rows(template_id: str):
    size = TEMPLATES[template_id].required_bars
    ts = "000001.SZ"
    dates = [date(2026, 9, 18) - timedelta(days=size - 1 - i) for i in range(size)]
    closes = [10.0] * size
    volumes = [100.0] * size
    ma5, ma20, ma60 = [10.0] * size, [10.0] * size, [9.0] * size
    lower, macd, rsi = [9.0] * size, [0.1] * size, [55.0] * size
    if template_id == "ma_trend_cross_v1":
        closes[-1] = 10.3
        ma5[-2:], ma20[-2:] = [10.0, 10.2], [10.05, 10.1]
        ma60[-1], macd[-2:] = 9.8, [0.05, 0.1]
    elif template_id == "trend_pullback_v1":
        closes[-2:], lower[-2:] = [9.0, 9.6], [9.1, 9.2]
        ma20[-1], rsi[-2:], macd[-2:] = 9.4, [38.0, 44.0], [-0.1, 0.0]
    elif template_id == "volume_surge_confirm_v1":
        closes[-2:], volumes[-1], ma20[-1] = [10.0, 10.2], 220.0, 9.8
    factors = [
        (ts, d, ma5[i], ma20[i], ma60[i], 10.0, 11.0, lower[i], 0.2, 0.1, macd[i], rsi[i])
        for i, d in enumerate(dates)
    ]
    daily = [
        (ts, d, closes[i], closes[i], closes[i], closes[i], volumes[i], closes[i] * volumes[i])
        for i, d in enumerate(dates)
    ]
    # loader 查询结果约定每票按日期降序。
    return (
        list(reversed(daily)), list(reversed(factors)),
        list(reversed([(ts, d, 1.0) for d in dates])),
        [(ts, dates[-1], False, False, closes[-1] * 1.1, closes[-1] * 0.9, "MAIN")],
    )


@pytest.mark.parametrize(
    "template_id",
    ["ma_trend_cross_v1", "trend_pullback_v1", "volume_surge_confirm_v1"],
)
def test_first_wave_uses_real_loader_and_runner(template_id):
    definition = TEMPLATES[template_id]
    item = MarketContextBatchLoader(lookback=definition.required_bars).load_batch(
        _MarketConn(*_rows(template_id)), ["000001.SZ"], date(2026, 9, 18)
    )[0]
    assert item["status"] == "OK"
    assert validate_template_context(definition, item["context"]) is None
    result = run_strategy(definition.render()[0], item["context"], timeout=2)
    assert result.ok
    assert result.output["action"] == "BUY"


def test_ex_dividend_adjustment_does_not_create_mechanical_cross_or_stop():
    ts = "000001.SZ"
    dates = [date(2026, 9, 16), date(2026, 9, 17), date(2026, 9, 18)]
    # 9/18 发生 10 送 10：raw 20 -> 10，adj 1 -> 2；qfq 序列应连续为 10。
    raw = [20.0, 20.0, 10.0]
    adj_values = [1.0, 1.0, 2.0]
    daily = [(ts, d, p, p, p, p, 100.0, p * 100.0) for d, p in zip(dates, raw)]
    factors = [(ts, d, 10.0, 10.0, 9.0, 10.0, 11.0, 9.0, 0.2, 0.1, 0.1, 55.0) for d in dates]
    conn = _MarketConn(
        list(reversed(daily)), list(reversed(factors)),
        list(reversed([(ts, d, a) for d, a in zip(dates, adj_values)])),
        [(ts, dates[-1], False, False, 11.0, 9.0, "MAIN")],
    )
    item = MarketContextBatchLoader(lookback=3).load_batch(conn, [ts], dates[-1])[0]
    assert item["status"] == "OK"
    assert item["context"]["ohlcv"]["close"] == [10.0, 10.0, 10.0]
    # raw 价格会因送转从 20 机械跳到 10，若把除权日前 raw 止损 18 直接沿用，
    # 会产生假止损；qfq 口径仍为 10 > 9。三价分别映射到任一 raw 版本后仍严格有序。
    assert Decimal(str(raw[-1])) < Decimal("18")
    assert Decimal(str(item["context"]["ohlcv"]["close"][-1])) > Decimal("9")
    before = [
        PriceBasisMapper.qfq_to_raw(value, qfq_close=10, raw_close=20)
        for value in (10, 9, 13)
    ]
    after = [
        PriceBasisMapper.qfq_to_raw(value, qfq_close=10, raw_close=10)
        for value in (10, 9, 13)
    ]
    assert before[1] < before[0] < before[2]
    assert after[1] < after[0] < after[2]
    result = run_strategy(TEMPLATES["ma_trend_cross_v1"].render()[0], item["context"], timeout=2)
    assert result.ok and result.output["action"] == "HOLD"


def test_loader_exposes_same_day_atr_to_trusted_host():
    daily, factors, adj, status = _rows("ma_trend_cross_v1")
    factors = [(*row, 0.25) for row in factors]
    item = MarketContextBatchLoader(lookback=TEMPLATES["ma_trend_cross_v1"].required_bars).load_batch(
        _MarketConn(daily, factors, adj, status), ["000001.SZ"], date(2026, 9, 18),
    )[0]
    assert item["status"] == "OK" and item["execution_market"]["atr_qfq"] == 0.25
    # This synthetic daily fixture skips the exchange-session proof. The
    # loader must expose unknown ADV20 instead of deriving it from 250 bars.
    assert item["execution_market"]["adv20_amount"] is None
