"""
事件研究法核心测试（方案 3.6.3：模拟数据测试）

覆盖：
- t0 交易日对齐规则（盘前/盘中/盘后/非交易日）
- 窗口切分与估计窗口
- 市场模型 OLS + CAR 计算（模拟数据注入事件冲击）
- 方向判定
"""

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from AI.eventStudy.processing import event_study


# ==================== t0 规则 ====================

_WEEKDAYS = {
    date(2026, 8, 17),  # 周一
    date(2026, 8, 18), date(2026, 8, 19), date(2026, 8, 20), date(2026, 8, 21),
    date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 26), date(2026, 8, 27),
}

# 与项目交易日历工具同约定：入参为 ISO 日期字符串
_weekday_fn = lambda s: date.fromisoformat(s).weekday() < 5  # noqa: E731


def test_resolve_t0_pre_market_on_trading_day():
    """盘前（09:30 前）公布 → t0 = 当日（交易日）。"""
    announced = datetime(2026, 8, 18, 8, 30)  # 周二 08:30
    assert event_study.resolve_t0(announced, _WEEKDAYS) == date(2026, 8, 18)


def test_resolve_t0_pre_market_on_weekend():
    """盘前 + 非交易日 → t0 = 下一交易日。"""
    announced = datetime(2026, 8, 23, 7, 0)  # 周日
    assert event_study.resolve_t0(announced, _WEEKDAYS) == date(2026, 8, 24)


def test_resolve_t0_intraday():
    """盘中公布 → t0 = 下一交易日。"""
    announced = datetime(2026, 8, 18, 11, 0)  # 周二盘中
    assert event_study.resolve_t0(announced, _WEEKDAYS) == date(2026, 8, 19)


def test_resolve_t0_after_close():
    """盘后公布 → t0 = 下一交易日。"""
    announced = datetime(2026, 8, 18, 16, 0)  # 周二盘后
    assert event_study.resolve_t0(announced, _WEEKDAYS) == date(2026, 8, 19)


def test_resolve_t0_fallback_weekday_heuristic():
    """无交易日历时以工作日兜底。"""
    announced = datetime(2026, 8, 21, 14, 0)  # 周五盘中
    assert event_study.resolve_t0(announced, None, _weekday_fn) == date(2026, 8, 24)


# ==================== 窗口切分 ====================

def test_window_dates():
    days = [date(2026, 8, 3 + i) for i in range(15) if date(2026, 8, 3 + i).weekday() < 5]
    t0 = days[5]
    pre = event_study._window_dates(days, t0, "pre_event_5d", 5, 5)
    assert pre == days[0:5]
    assert event_study._window_dates(days, t0, "event_day", 5, 5) == [t0]
    assert event_study._window_dates(days, t0, "post_event_5d", 5, 5) == days[6:11]


def test_estimation_dates():
    start = date(2026, 8, 3)
    days = [start + timedelta(days=i) for i in range(80)
            if (start + timedelta(days=i)).weekday() < 5]
    t0 = days[30]
    est = event_study._estimation_dates(days, t0, estimation_days=20, gap_days=10)
    assert est == days[0:20]  # [t0-30, t0-10)


# ==================== 方向判定 ====================

def test_judge_direction():
    assert event_study._judge_direction(0.02, 3.0) == 1
    assert event_study._judge_direction(-0.02, -3.0) == -1
    assert event_study._judge_direction(0.003, 0.5) == 0   # CAR 未过阈值
    assert event_study._judge_direction(0.02, 0.5) == 0    # t 未过阈值
    assert event_study._judge_direction(0.02, None) == 0   # 无 t 统计量


# ==================== 完整事件研究（模拟数据） ====================

class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConn:
    """按 SQL 子串分派返回结果的最小连接桩。"""

    def __init__(self, matcher):
        self._matcher = matcher

    def execute(self, sql, params=None):
        rows = self._matcher(sql, params)
        return FakeCursor(rows if rows is not None else [])

    def commit(self):
        pass

    def close(self):
        pass


@pytest.fixture
def simulated_data():
    """构造资产/市场模拟行情：资产 = 0.8×市场 + 噪声，t0 后 +1%/日事件冲击。"""
    rng = np.random.default_rng(42)
    t0 = date(2026, 3, 18)  # 周三
    days = pd.bdate_range(t0 - pd.Timedelta(days=600), t0 + pd.Timedelta(days=60))
    n = len(days)
    market_ret = rng.normal(0.0002, 0.008, n)
    market_close = 3000 * np.exp(np.cumsum(market_ret))
    market_close[0] = 3000.0

    noise = rng.normal(0, 0.002, n)
    event_effect = np.where(days > pd.Timestamp(t0), 0.01, 0.0)  # t0 后持续 +1%
    asset_ret = 0.8 * market_ret + noise + event_effect
    asset_close = 3000 * np.exp(np.cumsum(asset_ret))
    asset_close[0] = 3000.0

    def make_df(closes, tss):
        return pd.DataFrame({
            "ts": tss,
            "open": closes,
            "high": closes,
            "low": closes,
            "adj_close": closes,
            "vol": 1e8,
            "amount": 5e10,
        })

    return {
        "t0": t0,
        "days": days,
        "asset_df": make_df(asset_close, days),
        "market_df": make_df(market_close, days),
    }


@pytest.fixture
def fake_conn(simulated_data):
    t0 = simulated_data["t0"]

    def matcher(sql, params=None):
        if "SELECT trading_day FROM events" in sql:
            return [(t0,)]
        if "SELECT COUNT(*) FROM events" in sql:  # 污染检查
            return [(0,)]
        return None

    return FakeConn(matcher)


def test_run_event_study_simulated(fake_conn, simulated_data, monkeypatch):
    """模拟事件冲击 +1%/日（t0 后 5 日）：post_event_5d CAR ≈ 5%，方向利好。"""
    t0 = simulated_data["t0"]
    asset_df, market_df = simulated_data["asset_df"], simulated_data["market_df"]

    def fake_get_market_data(conn, asset_id, start_date, end_date):
        df = asset_df if asset_id == 1 else market_df
        start_ts = pd.Timestamp(start_date)
        end_ts = pd.Timestamp(end_date)
        return df[(df["ts"] >= start_ts) & (df["ts"] < end_ts)].reset_index(drop=True)

    monkeypatch.setattr(
        "AI.eventStudy.db.market_data_dao.get_market_data", fake_get_market_data
    )

    r = event_study.run_event_study(
        fake_conn, event_id=1, asset_id=1, market_asset_id=2,
        window_type="post_event_5d",
    )
    assert r.get("error") is None, r
    assert r["cumulative_abnormal_return"] > 0.03  # 5 日 × ~1%
    assert r["direction"] == 1
    assert r["t_stat"] is not None and r["t_stat"] > 1.96
    assert r["is_contaminated"] is False


def test_run_event_study_event_day_neutral(fake_conn, simulated_data, monkeypatch):
    """事件当日无冲击：event_day CAR 近零，方向中性。"""
    asset_df, market_df = simulated_data["asset_df"], simulated_data["market_df"]

    def fake_get_market_data(conn, asset_id, start_date, end_date):
        df = asset_df if asset_id == 1 else market_df
        start_ts = pd.Timestamp(start_date)
        end_ts = pd.Timestamp(end_date)
        return df[(df["ts"] >= start_ts) & (df["ts"] < end_ts)].reset_index(drop=True)

    monkeypatch.setattr(
        "AI.eventStudy.db.market_data_dao.get_market_data", fake_get_market_data
    )

    r = event_study.run_event_study(
        fake_conn, event_id=1, asset_id=1, market_asset_id=2, window_type="event_day",
    )
    assert r.get("error") is None, r
    assert abs(r["cumulative_abnormal_return"]) < 0.01
    assert r["direction"] == 0


def test_run_event_study_insufficient_data():
    """t0 未对齐时应返回明确错误（不抛异常）。"""
    conn = FakeConn(lambda sql, params: None)
    r = event_study.run_event_study(conn, 1, 1, 2, "event_day")
    assert r.get("error")
