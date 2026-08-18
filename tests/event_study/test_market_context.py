"""
市场上下文模块测试（方案 3.5）

覆盖：
- T-1 匹配规则（盘前/盘中 → 前一交易日；盘后 → 当日；非交易日 → 最近已收盘日）
- compute_market_context 指标计算（收益/波动/成交额/利率）
- 爬虫时间解析与标准化
"""

from datetime import date, datetime, time

import pandas as pd
import pytest

from AI.eventStudy.processing import market_context
from AI.eventStudy.collectors import event_crawler


# ==================== T-1 匹配规则（3.5.1） ====================

def _weekday_is_trading(d):
    return d.weekday() < 5


@pytest.fixture(autouse=True)
def _patch_calendar(monkeypatch):
    """测试中以工作日近似交易日。"""
    monkeypatch.setattr(market_context, "_is_trading_day", _weekday_is_trading)


def test_context_date_pre_market():
    """盘前事件 → 前一交易日。"""
    d = market_context.resolve_context_date(datetime(2026, 8, 18, 8, 0))  # 周二盘前
    assert d == date(2026, 8, 17)  # 周一


def test_context_date_intraday():
    """盘中事件 → 前一交易日。"""
    d = market_context.resolve_context_date(datetime(2026, 8, 18, 11, 0))  # 周二盘中
    assert d == date(2026, 8, 17)


def test_context_date_after_close():
    """盘后事件 → 当日收盘。"""
    d = market_context.resolve_context_date(datetime(2026, 8, 18, 16, 0))  # 周二盘后
    assert d == date(2026, 8, 18)


def test_context_date_weekend():
    """非交易日 → 最近已收盘交易日（周五）。"""
    d = market_context.resolve_context_date(datetime(2026, 8, 23, 12, 0))  # 周日
    assert d == date(2026, 8, 21)


# ==================== compute_market_context ====================

class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self, matcher):
        self._matcher = matcher

    def execute(self, sql, params=None):
        rows = self._matcher(sql, params)
        return FakeCursor(rows if rows is not None else [])

    def commit(self):
        pass


@pytest.fixture
def ctx_data():
    """构造基准指数 30 个交易日行情：日收益 1%，成交额 5e10。"""
    days = pd.bdate_range("2026-06-01", periods=30)
    closes = 3000 * (1.01 ** pd.Series(range(30)))
    return days, closes


def test_compute_market_context_indicators(ctx_data, monkeypatch):
    days, closes = ctx_data
    monkeypatch.setattr(
        market_context, "fetch_macro_context", lambda d, m="CN": {"rate_10y": 2.3}
    )
    conn = FakeConn(lambda sql, params: [(1,)] if "FROM assets" in sql else None)
    monkeypatch.setattr(
        market_context.market_data_dao, "get_market_data",
        lambda conn, asset_id, start, end: pd.DataFrame({
            "ts": days,
            "open": closes, "high": closes, "low": closes,
            "adj_close": closes, "vol": 1e8, "amount": 5e10,
        }),
    )
    ind = market_context.compute_market_context(conn, days[-1].date())
    # 日收益 1% 复利 20 日 → (1.01^20 - 1) ≈ 0.2202
    assert ind["return_20d"] == pytest.approx(0.2202, abs=1e-3)
    assert ind["vol_20d"] == pytest.approx(0.0, abs=1e-6)          # 恒收益 → 波动 0
    assert ind["amount_20d"] == pytest.approx(5e10)
    assert ind["rate_10y"] == 2.3


def test_compute_market_context_insufficient_data(ctx_data, monkeypatch):
    days, closes = ctx_data
    monkeypatch.setattr(
        market_context.market_data_dao, "get_market_data",
        lambda conn, asset_id, start, end: pd.DataFrame({
            "ts": days[:5], "open": closes[:5], "high": closes[:5],
            "low": closes[:5], "adj_close": closes[:5], "vol": 1e8, "amount": 5e10,
        }),
    )
    conn = FakeConn(lambda sql, params: [(1,)])
    ind = market_context.compute_market_context(conn, days[4].date())
    assert "return_20d" not in ind  # 数据不足 21 日，指标缺省


# ==================== 爬虫时间解析 ====================

def test_iso_ts_formats():
    assert event_crawler._iso_ts("2024-02-10 09:30:00") == "2024-02-10T09:30:00+08:00"
    assert event_crawler._iso_ts("2024-02-10 09:30") == "2024-02-10T09:30:00+08:00"
    assert event_crawler._iso_ts("2024-02-10T09:30:00+08:00") == "2024-02-10T09:30:00+08:00"
    assert event_crawler._iso_ts(1707528600) == "2024-02-10T09:30:00+08:00"
    assert event_crawler._iso_ts("垃圾时间") is None
    assert event_crawler._iso_ts(None) is None


def test_normalize_skips_bad_time():
    ev = event_crawler._normalize("标题", "内容", "http://x", "bad-time", source="测试")
    assert ev is None
    ev = event_crawler._normalize("标题", "内容", "http://x", "2024-02-10 09:30:00",
                                  importance_hint=5, source="测试")
    assert ev["title"] == "标题"
    assert ev["importance_hint"] == 5
    assert ev["source"] == "测试"
