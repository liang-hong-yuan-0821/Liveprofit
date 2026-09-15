"""单元测试：个股技术指标报告（stk_factor_pro 因子映射，不自算；技术指标数据源切换方案 §3.4）。

- 14 项映射逐值断言（REPORT_INDICATORS 与报告行一一对应）
- 非交易日 / 无数据源 / None / NaN → N/A 降级
- 日期回退：取 <= curr_date 的最新一行（报告 header 日期仍为入参 curr_date）
"""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from AI.dataflows.technical.stockstats import REPORT_INDICATORS, StockstatsUtils


def _factor_df(trade_date: str = "2026-09-11", **overrides) -> pd.DataFrame:
    row = {
        "trade_date": trade_date,
        **{field: float(i) + 1.0 for i, (field, _) in enumerate(REPORT_INDICATORS)},
    }
    row.update(overrides)
    return pd.DataFrame([row])


def _patch(monkeypatch, df):
    fake = MagicMock(return_value=df)
    monkeypatch.setattr("AI.dataflows.interface.get_stock_factor_df", fake)
    return fake


def test_report_maps_all_14_indicators(monkeypatch):
    fake = _patch(monkeypatch, _factor_df())
    report = StockstatsUtils.get_indicators_report("000001.SZ", "2026-09-11")

    assert report.startswith("技术指标报告 - 000001.SZ @ 2026-09-11")
    # 14 项全部按 (字段, 键) 映射，值逐项正确（float(i)+1.0）
    lines = [line.strip() for line in report.splitlines() if line.strip().startswith(("ma_", "rsi", "macd", "boll", "close"))]
    assert len(lines) == 14
    for i, (_, display) in enumerate(REPORT_INDICATORS):
        assert any(line == f"{display}: {float(i) + 1.0}" for line in lines), f"缺 {display} 映射"

    # 取数窗口：start = curr - 30 天（位置参数：ticker, start_date, end_date）
    args = fake.call_args[0]
    assert args[1] == "2026-08-12"
    assert args[2] == "2026-09-11"


def test_report_falls_back_to_previous_trading_day(monkeypatch):
    _patch(monkeypatch, _factor_df(trade_date="2026-09-10"))
    report = StockstatsUtils.get_indicators_report("000001.SZ", "2026-09-12")
    # 值取 09-10 行（非交易日回退），header 日期仍为入参
    assert "技术指标报告 - 000001.SZ @ 2026-09-12" in report
    assert "close_5_sma: 1.0" in report


def test_report_unsupported_source_returns_na(monkeypatch):
    _patch(monkeypatch, None)
    report = StockstatsUtils.get_indicators_report("000001.SZ", "2026-09-11")
    assert report == "N/A: 技术因子数据不可用（数据源不支持或上游取数失败）"


def test_report_non_trading_day_returns_na(monkeypatch):
    _patch(monkeypatch, pd.DataFrame())
    report = StockstatsUtils.get_indicators_report("000001.SZ", "2026-09-11")
    assert report == "N/A: 区间内无行情数据 (停牌/非交易日)"


def test_report_none_and_nan_values_fall_back_to_na(monkeypatch):
    _patch(monkeypatch, _factor_df(ma_bfq_5=None, rsi_bfq_6=float("nan")))
    report = StockstatsUtils.get_indicators_report("000001.SZ", "2026-09-11")
    assert "close_5_sma: N/A" in report
    assert "rsi_6: N/A" in report
    # 其余字段正常取值
    assert "boll_ub: 13.0" in report


def test_lookback_days_signature_is_ignored(monkeypatch):
    # lookback_days 仅保留签名：传任意值不影响取数窗口（固定 30 天）
    fake = _patch(monkeypatch, _factor_df())
    StockstatsUtils.get_indicators_report("000001.SZ", "2026-09-11", lookback_days=999)
    args = fake.call_args[0]
    assert args[1] == "2026-08-12"
